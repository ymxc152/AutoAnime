"""RSS 轮询器（E4a）：拉取 → seen 去重 → 对齐 → 择优 → 提交下载。

一轮 poll 的确定性流程（FlexGet accept/reject/seen 语义 + ARCHITECTURE §2/§4）：

1. 启用的 rss_sources 逐个处理；按季状态降频（cadence.should_poll_season）；
2. 拉取 feed（重试 ``fetch_retries`` 次、指数退避；仍失败 → 跳过本轮，
   不 crash 不告警风暴——Mikan 被墙地区的常态路径）；
3. 条目 seen 去重：``release_record`` 按 ``torrent_hash``（infohash 唯一
   约束兜底）与 ``source_url``（guid/torrent 地址）双键查重；
4. 条目对齐（expected = 订阅的番/季，organize.expected.align_rss_entry）：
   ``fast_path``/同番命中集 → 候选；``conflict``/段不支持 → reject（不下载
   错标源）；``unparsed`` → 跳过本轮不落库（FlexGet backlog 语义：记忆
   飞轮学习后可能解析得出）；
5. 同一集的多个新候选走评分公式（organize.upgrade，seeders 未知 → 0 分
   参与不剔除，D15）取最高分；MISSING → 直接下最高分；ORGANIZED → 过
   洗版阈值（decide_upgrade）；DOWNLOADING 等状态 → 不重复下（A7 幂等）；
6. 提交网关（.torrent 字节 → 本地算 infohash → add）：release
   candidate → picked（accepted），episode MISSING → DOWNLOADING；网关
   失败 → release 置 failed（reason 落库），episode 保持 MISSING 等下轮。

expected 载体：release_record(episode_id, torrent_hash) 在候选落库时即
写入（D13），下载完成侧据此组装 per-file expected。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr

from autoanime.core.enums import Decision, EpisodeState, ReleaseStatus, SeasonState
from autoanime.core.events import Event, EventBus, EventCategory
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.core.models import Episode, ReleaseRecord, RssSource
from autoanime.gateway import GatewayError
from autoanime.gateway.rss import FeedPage, RssEntry, fetch_feed, fetch_torrent
from autoanime.gateway.torrents import torrent_info_hash
from autoanime.organize.expected import ExpectedContext, align_rss_entry
from autoanime.organize.upgrade import decide_upgrade, score_from_title
from autoanime.scheduler.cadence import should_poll_season
from autoanime.scheduler.missing import EpisodeFact, season_gap, today_jst
from autoanime.scheduler.store import LoopStore, TransitionError

logger = logging.getLogger(__name__)


@dataclass
class SourceOutcome:
    """单源处理小计（CLI rerun / 报表 / 通知共用；处理过程中累加）。"""

    source_id: int
    season_id: int
    skipped_not_due: bool = False
    fetch_error: str | None = None
    entries_total: int = 0
    seen: int = 0
    rejected: int = 0
    backlog: int = 0
    picked: int = 0
    gaps: tuple[int, ...] = ()


@dataclass(frozen=True)
class RssPollReport:
    """一轮全源轮询汇总。"""

    outcomes: tuple[SourceOutcome, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def picked(self) -> int:
        return sum(outcome.picked for outcome in self.outcomes)

    @property
    def all_gaps(self) -> dict[int, tuple[int, ...]]:
        return {o.season_id: o.gaps for o in self.outcomes if o.gaps}


@dataclass(frozen=True)
class _Candidate:
    """一个待择优的条目（同集内比分）。"""

    entry: RssEntry
    infohash: str
    data: bytes
    parse: ParseResult
    score: float


class RssPoller:
    """订阅轮询器：所有状态进库，进程内不持可变内存态。

    网络客户端经 ``client_factory`` 注入（测试用 MockTransport）；退避睡眠
    经 ``sleeper`` 注入（测试零等待）；随机源 ``rng`` 只用于未来抖动扩展。
    """

    def __init__(
        self,
        store: LoopStore,
        orchestrator: Any,
        gateway: Any,
        *,
        bus: EventBus | None = None,
        fetch_retries: int = 2,
        fetch_timeout_s: float = 30.0,
        upgrade_threshold: float = 2.0,
        upgrade_max_per_episode: int = 2,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        rss_token: SecretStr | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._gateway = gateway
        self._bus = bus
        self._fetch_retries = fetch_retries
        self._fetch_timeout_s = fetch_timeout_s
        self._upgrade_threshold = upgrade_threshold
        self._upgrade_max_per_episode = upgrade_max_per_episode
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=fetch_timeout_s)
        )
        self._rss_token = rss_token
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------ entry

    async def poll_all(self, *, now: datetime) -> RssPollReport:
        """轮询全部启用源（顺序执行；单源故障不拖垮整轮）。"""
        outcomes: list[SourceOutcome] = []
        errors: list[str] = []
        for source in await self._store.enabled_rss_sources():
            try:
                outcomes.append(await self.poll_source(source, now=now))
            except Exception as exc:  # noqa: BLE001 — 见 docstring
                logger.warning("rss source %s failed: %s", source.id, exc)
                errors.append(f"source {source.id}: {type(exc).__name__}")
        return RssPollReport(outcomes=tuple(outcomes), errors=tuple(errors))

    async def poll_source(self, source: RssSource, *, now: datetime) -> SourceOutcome:
        binding = await self._store.season_series(source.season_id)
        if binding is None:
            return SourceOutcome(
                source_id=source.id, season_id=source.season_id,
                fetch_error="season/series missing",
            )
        season, series = binding
        season_status = SeasonState(
            season.status.value if hasattr(season.status, "value") else season.status
        )
        if not should_poll_season(
            season_status=season_status,
            last_polled_at=source.last_polled_at,
            now=now,
        ):
            return SourceOutcome(source_id=source.id, season_id=season.id, skipped_not_due=True)

        episodes = await self._store.episodes_for_season(season.id)
        gap = season_gap(
            [self._fact(row) for row in episodes],
            today=today_jst(now),
            season_id=season.id,
        )
        outcome = SourceOutcome(
            source_id=source.id, season_id=season.id, gaps=gap.aired_missing
        )

        expected_base = ExpectedContext(
            series_id=series.id,
            season_number=season.number,
            episode_number=0,  # per-entry 覆盖
            title_cn=series.title_cn,
            title_jp=series.title_jp,
            title_romaji=series.title_romaji,
            fansub_pref=series.fansub_pref,
        )
        context = ParseContext(
            known_series=series.id,
            release_progress=gap.released_progress or None,
            fansub_pref=series.fansub_pref,
        )

        try:
            async with self._client_factory() as client:
                page = await self._fetch_with_retry(client, source)
                if page is None:
                    outcome.fetch_error = "unreachable after retries"
                    return outcome
                outcome.entries_total = len(page.entries)
                await self._process_entries(
                    page, client=client, source=source, expected_base=expected_base,
                    context=context, episodes=episodes, now=now, outcome=outcome,
                )
        finally:
            await self._store.mark_polled(source.id, now)

        if outcome.picked:
            await self._publish(
                EventCategory.DOWNLOAD,
                "download.picked",
                {"source_id": source.id, "picked": outcome.picked},
            )
        if gap.has_gap:
            # D15：缺口报告 + 通知；回补 = 等 RSS 自然命中（本轮新 picked
            # 即是回补命中），v1 不主动搜索。
            await self._publish(
                EventCategory.NOTIFY,
                "episode.gap",
                {"season_id": season.id, "gap": list(gap.aired_missing)},
            )
        return outcome

    # ------------------------------------------------------------------ fetch

    async def _fetch_with_retry(
        self, client: httpx.AsyncClient, source: RssSource
    ) -> FeedPage | None:
        """重试 + 指数退避；仍失败返回 None（跳过本轮，不 crash）。"""
        last_error: str | None = None
        for attempt in range(self._fetch_retries + 1):
            try:
                return await fetch_feed(
                    client,
                    source.url,
                    token=SecretStr(source.token) if source.token else self._rss_token,
                )
            except Exception as exc:  # noqa: BLE001 — 网络/解析失败统一退避
                last_error = type(exc).__name__
                logger.info(
                    "rss fetch attempt %s failed for source %s: %s",
                    attempt + 1, source.id, last_error,
                )
                if attempt < self._fetch_retries:
                    await self._sleeper(min(2**attempt, 8))
        logger.warning("rss source %s skipped this round: %s", source.id, last_error)
        return None

    # ---------------------------------------------------------------- entries

    async def _process_entries(
        self,
        page: FeedPage,
        *,
        client: httpx.AsyncClient,
        source: RssSource,
        expected_base: ExpectedContext,
        context: ParseContext,
        episodes: list[Episode],
        now: datetime,
        outcome: SourceOutcome,
    ) -> None:
        """逐条目分流 + 同集候选择优（seen/rejected/backlog/picked 计数）。"""
        candidates: dict[int, list[_Candidate]] = {}
        rejects: list[tuple[RssEntry, str, bytes]] = []
        batch_hashes: set[str] = set()  # 批内去重（同一种子多条 guid/镜像）
        for entry in page.entries:
            verdict = await self._handle_entry(
                entry,
                client=client,
                source=source,
                expected_base=expected_base,
                context=context,
                candidates=candidates,
                rejects=rejects,
                batch_hashes=batch_hashes,
            )
            if verdict == "seen":
                outcome.seen += 1
            elif verdict == "rejected":
                outcome.rejected += 1
            elif verdict == "backlog":
                outcome.backlog += 1
        for entry, reason, data in rejects:
            await self._record_reject(entry, reason, data, source)
        outcome.picked = await self._resolve_candidates(candidates, episodes, source, now)

    async def _handle_entry(
        self,
        entry: RssEntry,
        *,
        client: httpx.AsyncClient,
        source: RssSource,
        expected_base: ExpectedContext,
        context: ParseContext,
        candidates: dict[int, list[_Candidate]],
        rejects: list[tuple[RssEntry, str, bytes]],
        batch_hashes: set[str],
    ) -> str:
        """处理单条目：seen / rejected / backlog / candidate。"""
        del source
        if (
            await self._store.find_release_by_source_url(entry.guid) is not None
            or await self._store.find_release_by_source_url(entry.torrent_url) is not None
        ):
            return "seen"
        try:
            data = await fetch_torrent(client, entry.torrent_url)
            infohash = torrent_info_hash(data)
        except Exception as exc:  # noqa: BLE001 — 取种/解析失败按 backlog 重试
            logger.info("torrent fetch failed for %s: %s", entry.guid, type(exc).__name__)
            return "backlog"
        if infohash in batch_hashes or await self._store.find_release_by_hash(infohash) is not None:
            return "seen"
        batch_hashes.add(infohash)

        parse = await self._parse(entry.title, context)
        alignment = align_rss_entry(
            parse,
            expected_titles=expected_base.titles(),
            season_number=expected_base.season_number,
        )
        if alignment.verdict == "conflict":
            rejects.append((entry, f"expected_conflict: {alignment.detail}", data))
            return "rejected"
        if parse is None or alignment.verdict == "unparsed":
            return "backlog"
        if parse.segment.value != "episode" or alignment.parsed_episode is None:
            # SEASON_PACK/MOVIE/无集数：Mikan 订阅不支持（Plan §6 实操坑），
            # 该类走散装导入路径；RSS 轮询侧确定性地拒绝。
            rejects.append(
                (entry, f"segment_not_supported: {parse.segment.value}", data)
            )
            return "rejected"
        candidates.setdefault(alignment.parsed_episode, []).append(
            _Candidate(
                entry=entry,
                infohash=infohash,
                data=data,
                parse=parse,
                score=score_from_title(
                    entry.title,
                    fansub=parse.fansub,
                    fansub_pref=expected_base.fansub_pref,
                    seeders=None,  # RSS 不带做种数：0 分参与不剔除（D15）
                ),
            )
        )
        return "candidate"

    async def _parse(self, title: str, context: ParseContext) -> ParseResult | None:
        parse_method = getattr(self._orchestrator, "parse", None)
        if not callable(parse_method):
            raise RuntimeError("orchestrator must expose parse()")
        result: Any = parse_method(RawName(name=title), context)
        return await result

    # ------------------------------------------------------------------ pick

    async def _resolve_candidates(
        self,
        candidates: dict[int, list[_Candidate]],
        episodes: list[Episode],
        source: RssSource,
        now: datetime,
    ) -> int:
        """同集候选择优 + 分状态决策 + 提交网关（幂等收口在 store）。"""
        episode_by_number = {row.number: row for row in episodes}
        picked = 0
        for number, group in candidates.items():
            group.sort(key=lambda c: c.score, reverse=True)
            episode = episode_by_number.get(number)
            if episode is None:
                for candidate in group:
                    await self._store.create_release(
                        ReleaseRecord(
                            season_id=source.season_id,
                            torrent_hash=candidate.infohash,
                            fansub=candidate.parse.fansub,
                            size=candidate.entry.size,
                            score=candidate.score,
                            decision=Decision.REJECTED,
                            reason="episode_not_in_season",
                            source_url=candidate.entry.guid,
                        )
                    )
                continue
            state = self._state(episode)
            best = group[0]
            if state is EpisodeState.DOWNLOADING:
                await self._record_pending(best, episode.id, "already_downloading")
                continue
            if state is EpisodeState.ORGANIZED:
                decision = decide_upgrade(
                    candidate_score=best.score,
                    current_score=float(episode.quality_score or 0.0),
                    upgraded_count=int(episode.upgraded_count or 0),
                    threshold=self._upgrade_threshold,
                    max_upgrades=self._upgrade_max_per_episode,
                )
                if not decision.allowed:
                    await self._record_pending(
                        best, episode.id, f"upgrade: {decision.reason}"
                    )
                    continue
            elif state is not EpisodeState.MISSING:
                # DOWNLOADED/UPGRADED/IGNORED/FLAGGED：v1 不自动重下。
                await self._record_pending(
                    best, episode.id, f"episode_state_{state.value}"
                )
                continue
            if await self._submit(best, source, episode.id, now):
                picked += 1
        return picked

    async def _record_pending(
        self, candidate: _Candidate, episode_id: int, reason: str
    ) -> None:
        await self._store.create_release(
            ReleaseRecord(
                episode_id=episode_id,
                torrent_hash=candidate.infohash,
                fansub=candidate.parse.fansub,
                size=candidate.entry.size,
                seeders=None,
                score=candidate.score,
                decision=Decision.PENDING,
                reason=reason,
                source_url=candidate.entry.guid,
            )
        )

    async def _record_reject(
        self, entry: RssEntry, reason: str, data: bytes, source: RssSource
    ) -> None:
        infohash = torrent_info_hash(data)  # reject 只在有 hash 时才走到这里
        if await self._store.find_release_by_hash(infohash) is not None:
            return
        await self._store.create_release(
            ReleaseRecord(
                season_id=source.season_id,
                torrent_hash=infohash,
                decision=Decision.REJECTED,
                reason=reason,
                source_url=entry.guid,
            )
        )

    async def _submit(
        self, candidate: _Candidate, source: RssSource, episode_id: int, now: datetime
    ) -> bool:
        """候选 → release 落库 → 网关提交 → picked + episode DOWNLOADING。"""
        del source
        record = await self._store.create_release(
            ReleaseRecord(
                episode_id=episode_id,
                torrent_hash=candidate.infohash,
                fansub=candidate.parse.fansub,
                size=candidate.entry.size,
                seeders=None,
                score=candidate.score,
                decision=Decision.PENDING,
                source_url=candidate.entry.guid,
            )
        )
        if record is None:
            return False  # 撞哈希：并发/重复提交，唯一约束兜底生效
        try:
            add = self._gateway.add_torrent_bytes
            await add(candidate.data)
        except GatewayError as exc:
            logger.warning("gateway add failed for %s: %s", candidate.infohash, exc)
            await self._store.transition_release(
                record.id,
                ReleaseStatus.FAILED,
                now=now,
                decision=Decision.REJECTED,
                reason=f"gateway: {exc}",
            )
            return False
        await self._store.transition_release(
            record.id, ReleaseStatus.PICKED, now=now, decision=Decision.ACCEPTED
        )
        try:
            await self._store.transition_episode(episode_id, EpisodeState.DOWNLOADING)
        except TransitionError:
            # 并发下另一路已转移（状态机守卫拒绝重复）；release 保持 picked，
            # 完成路径幂等（hash 唯一 + 状态机守卫双保险）。
            logger.info("episode %s already transitioning; skip state change", episode_id)
        return True

    # ------------------------------------------------------------------ misc

    @staticmethod
    def _state(row: Episode) -> EpisodeState:
        return EpisodeState(row.state.value if hasattr(row.state, "value") else row.state)

    @staticmethod
    def _fact(row: Episode) -> EpisodeFact:
        return EpisodeFact(number=row.number, state=RssPoller._state(row), air_date=row.air_date)

    async def _publish(self, category: EventCategory, message: str, payload: dict[str, object]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(Event(category=category, message=message, payload=payload))
        except Exception:  # noqa: BLE001 — 事件/通知永不致命
            logger.warning("event publish failed", exc_info=True)
