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
from autoanime.organize.expected import ExpectedContext, align_rss_entry, rule_reject_reason
from autoanime.organize.upgrade import decide_upgrade, score_from_title
from autoanime.scheduler.cadence import should_poll_season
from autoanime.scheduler.missing import EpisodeFact, season_gap, today_jst
from autoanime.scheduler.store import LoopStore, TransitionError

logger = logging.getLogger(__name__)

RSS_SOURCE_KIND_SEASON = "season"
RSS_SOURCE_KIND_AGGREGATE = "aggregate"


def source_kind(source: RssSource) -> str:
    """源类型（纯函数）：kind 列为空/未知值按季绑定源处理（旧行兼容）。"""
    kind = (getattr(source, "kind", None) or "").strip().lower()
    return kind if kind == RSS_SOURCE_KIND_AGGREGATE else RSS_SOURCE_KIND_SEASON


@dataclass
class SourceOutcome:
    """单源处理小计（CLI rerun / 报表 / 通知共用；处理过程中累加）。"""

    source_id: int
    # 聚合源（kind=aggregate）不绑季 → None（批次三）。
    season_id: int | None
    skipped_not_due: bool = False
    fetch_error: str | None = None
    entries_total: int = 0
    seen: int = 0
    rejected: int = 0
    backlog: int = 0
    # 聚合源专用：L1 解析成功但不命中任何活跃订阅的条目（不落库不进待确认）。
    ignored: int = 0
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


@dataclass(frozen=True)
class _AggregateTarget:
    """聚合源对齐目标：一个活跃订阅季（series × season + expected）。"""

    season: Any
    series: Any
    episodes: list[Episode]
    expected: ExpectedContext


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
        save_path: str | None = None,
        alias_service: Any | None = None,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._gateway = gateway
        # 显式下发保存路径:系统自己的任务恒落 settings.download_path
        # (qB 默认保存路径可能不同;与「下载目录」配置保持一致)
        self._save_path = save_path
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
        # 别名富化（P0-B）：AliasService 或鸭子类型兼容对象（提供
        # alias_titles_for）。测试可注入 fake；未注入时从 store 底层
        # storage 惰性解析（拿不到 = expected 不并别名，现状语义）。
        self._alias_service = alias_service

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
        # 批次三分流：聚合源（kind=aggregate）不绑季，走全订阅对齐链路。
        if source_kind(source) == RSS_SOURCE_KIND_AGGREGATE:
            return await self._poll_aggregate_source(source, now=now)
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
            include_keywords=series.include_keywords,
            exclude_keywords=series.exclude_keywords,
            extra_titles=await self._alias_titles(
                (series.title_cn, series.title_jp, series.title_romaji)
            ),
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

    # ------------------------------------------------------------- aggregate

    async def _poll_aggregate_source(
        self, source: RssSource, *, now: datetime
    ) -> SourceOutcome:
        """聚合源轮询（批次三）：一个 feed 混多部番 → 全订阅逐个对齐。

        与季绑定源的分工：无 season 绑定（cadence 按季降频不适用，幂等由
        episode 状态机 + torrent_hash 唯一约束兜底）。逐条目 L1 解析后对
        全部活跃订阅（series × season 构造 expected，含别名）做
        ``align_rss_entry``：命中 → 该 series/season 的既有候选链（源级
        include/exclude 先于 series 级规则）；未命中 → ignored 计数（不落
        库不进待确认）；源级规则拒绝 → rejected（不落库：无 season 绑定
        载体，下轮 seen 前置检查靠 source_url 命中不了，重取种代价可接受）。
        """
        outcome = SourceOutcome(source_id=source.id, season_id=None)
        try:
            async with self._client_factory() as client:
                page = await self._fetch_with_retry(client, source)
                if page is None:
                    outcome.fetch_error = "unreachable after retries"
                    return outcome
                outcome.entries_total = len(page.entries)
                targets = await self._aggregate_targets()
                await self._process_aggregate_entries(
                    page, client=client, source=source, targets=targets,
                    now=now, outcome=outcome,
                )
        finally:
            await self._store.mark_polled(source.id, now)
        if outcome.picked:
            await self._publish(
                EventCategory.DOWNLOAD,
                "download.picked",
                {"source_id": source.id, "picked": outcome.picked},
            )
        return outcome

    async def _aggregate_targets(self) -> list[_AggregateTarget]:
        """全部活跃订阅 → 对齐目标（series × season，含别名 expected）。"""
        targets: list[_AggregateTarget] = []
        for series in await self._store.list_series():
            if (series.status or "active") != "active":
                continue
            base_titles = (series.title_cn, series.title_jp, series.title_romaji)
            alias_titles = await self._alias_titles(base_titles)
            for season in await self._store.seasons_for_series(series.id):
                episodes = await self._store.episodes_for_season(season.id)
                targets.append(
                    _AggregateTarget(
                        season=season,
                        series=series,
                        episodes=episodes,
                        expected=ExpectedContext(
                            series_id=series.id,
                            season_number=season.number,
                            episode_number=0,  # per-entry 覆盖
                            title_cn=series.title_cn,
                            title_jp=series.title_jp,
                            title_romaji=series.title_romaji,
                            fansub_pref=series.fansub_pref,
                            include_keywords=series.include_keywords,
                            exclude_keywords=series.exclude_keywords,
                            extra_titles=alias_titles,
                        ),
                    )
                )
        return targets

    async def _process_aggregate_entries(
        self,
        page: FeedPage,
        *,
        client: httpx.AsyncClient,
        source: RssSource,
        targets: list[_AggregateTarget],
        now: datetime,
        outcome: SourceOutcome,
    ) -> None:
        """聚合源逐条目：seen → 源级规则 → 全订阅对齐 → 择优提交。"""
        candidates: dict[int, dict[int, list[_Candidate]]] = {}  # season_id → ep → []
        rejects: list[tuple[RssEntry, str, bytes, int | None]] = []  # + season_id
        batch_hashes: set[str] = set()
        for entry in page.entries:
            verdict = await self._handle_aggregate_entry(
                entry,
                client=client,
                source=source,
                targets=targets,
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
            elif verdict == "ignored":
                outcome.ignored += 1
        for entry, reason, data, season_id in rejects:
            await self._record_reject(entry, reason, data, source, season_id=season_id)
        for season_id, per_season in candidates.items():
            target = next((t for t in targets if t.season.id == season_id), None)
            if target is None:
                continue
            outcome.picked += await self._resolve_candidates(
                per_season,
                target.episodes,
                source,
                now,
                season_id=season_id,
            )

    async def _handle_aggregate_entry(
        self,
        entry: RssEntry,
        *,
        client: httpx.AsyncClient,
        source: RssSource,
        targets: list[_AggregateTarget],
        candidates: dict[int, dict[int, list[_Candidate]]],
        rejects: list[tuple[RssEntry, str, bytes, int | None]],
        batch_hashes: set[str],
    ) -> str:
        """聚合源单条目：seen / rejected / backlog / ignored / candidate。"""
        if (
            await self._store.find_release_by_source_url(entry.guid) is not None
            or await self._store.find_release_by_source_url(entry.torrent_url) is not None
        ):
            return "seen"
        # 源级全局规则先于一切对齐（聚合源级 include 白名单/exclude 黑名单）。
        source_rule_reason = rule_reject_reason(
            entry.title,
            include_keywords=source.include_keywords,
            exclude_keywords=source.exclude_keywords,
        )
        if source_rule_reason is not None:
            # 未取种即拒绝：无 infohash/无 season 载体 → 只计数不落库。
            rejects.append((entry, f"source_rule: {source_rule_reason}", b"", None))
            return "rejected"
        try:
            data = await fetch_torrent(client, entry.torrent_url)
            infohash = torrent_info_hash(data)
        except Exception as exc:  # noqa: BLE001 — 取种/解析失败按 backlog 重试
            logger.info("torrent fetch failed for %s: %s", entry.guid, type(exc).__name__)
            return "backlog"
        if infohash in batch_hashes or await self._store.find_release_by_hash(infohash) is not None:
            return "seen"
        batch_hashes.add(infohash)

        parse = await self._parse(entry.title, ParseContext())
        if parse is None:
            return "backlog"  # FlexGet backlog 语义：记忆飞轮学习后可能解析得出
        # 全部活跃订阅逐个对齐；首个人次命中即收口（确定性）。标题解析出
        # 季号时优先尝试同季目标（同番多季 feed 混排时防误挂前季）。
        ordered = targets
        if parse.season is not None:
            ordered = [
                *(t for t in targets if t.season.number == parse.season),
                *(t for t in targets if t.season.number != parse.season),
            ]
        for target in ordered:
            alignment = align_rss_entry(
                parse,
                expected_titles=target.expected.titles(),
                season_number=target.expected.season_number,
            )
            if alignment.verdict == "conflict":
                continue
            if alignment.verdict == "unparsed":
                return "backlog"
            if parse.segment.value != "episode" or alignment.parsed_episode is None:
                # SEASON_PACK/MOVIE/无集数：与季绑定源同口径确定性拒绝。
                rejects.append(
                    (entry, f"segment_not_supported: {parse.segment.value}", data, target.season.id)
                )
                return "rejected"
            # series 级规则叠加在源级之后（与选番抽屉「匹配预览」同一套）。
            rule_reason = rule_reject_reason(
                entry.title,
                include_keywords=target.expected.include_keywords,
                exclude_keywords=target.expected.exclude_keywords,
            )
            if rule_reason is not None:
                rejects.append((entry, rule_reason, data, target.season.id))
                return "rejected"
            candidates.setdefault(target.season.id, {}).setdefault(
                alignment.parsed_episode, []
            ).append(
                _Candidate(
                    entry=entry,
                    infohash=infohash,
                    data=data,
                    parse=parse,
                    score=score_from_title(
                        entry.title,
                        fansub=parse.fansub,
                        fansub_pref=target.expected.fansub_pref,
                        seeders=None,  # RSS 不带做种数：0 分参与不剔除（D15）
                    ),
                )
            )
            return "candidate"
        # 解析成功但不属于任何活跃订阅：ignored（不落库、不进待确认）。
        return "ignored"

    # ------------------------------------------------------------------ alias

    async def _alias_titles(self, titles: tuple[str | None, ...]) -> tuple[str, ...]:
        """订阅三标题之外的别名（title_aliases 查回）；失败/缺席返回空。

        别名富化是增益：服务缺席（fake store/未注入且底层 storage 不可达）
        或查询异常一律静默回空，绝不影响轮询主流程。
        """
        service = self._alias_service
        if service is None:
            storage = getattr(self._store, "_storage", None)
            if storage is None:
                return ()
            try:
                from autoanime.memory.alias import AliasService

                service = AliasService(storage)
                self._alias_service = service
            except Exception:  # noqa: BLE001 — 富化永不致命
                return ()
        lookup = getattr(service, "alias_titles_for", None)
        if not callable(lookup):
            return ()
        try:
            return await lookup(titles)
        except Exception:  # noqa: BLE001 — 富化永不致命
            logger.warning("alias titles lookup failed; expected without aliases")
            return ()

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
        outcome.picked = await self._resolve_candidates(
            candidates, episodes, source, now, season_id=source.season_id
        )

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
        # 通用 RSS 订阅规则（include 白名单 / exclude 黑名单,分号分隔关键词）:
        # 命中即确定性拒绝（选番抽屉「匹配预览」展示同一套规则）
        rule_reason = rule_reject_reason(
            entry.title,
            include_keywords=expected_base.include_keywords,
            exclude_keywords=expected_base.exclude_keywords,
        )
        if rule_reason is not None:
            rejects.append((entry, rule_reason, data))
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
        *,
        season_id: int | None = None,
    ) -> int:
        """同集候选择优 + 分状态决策 + 提交网关（幂等收口在 store）。

        ``season_id``：episode_not_in_season 拒绝记录的归属季（季绑定源 =
        source.season_id；聚合源 = 命中目标的 season id）。
        """
        target_season_id = season_id if season_id is not None else source.season_id
        episode_by_number = {row.number: row for row in episodes}
        picked = 0
        for number, group in candidates.items():
            group.sort(key=lambda c: c.score, reverse=True)
            episode = episode_by_number.get(number)
            if episode is None:
                if target_season_id is None:
                    continue  # 聚合源未绑季且无 episode 载体 → 无处落库，跳过
                for candidate in group:
                    await self._store.create_release(
                        ReleaseRecord(
                            season_id=target_season_id,
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
        self,
        entry: RssEntry,
        reason: str,
        data: bytes,
        source: RssSource,
        *,
        season_id: int | None = None,
    ) -> None:
        """拒绝落库（聚合源：命中目标的 season id；源级规则拒绝不落库）。"""
        target_season_id = season_id if season_id is not None else source.season_id
        if target_season_id is None or not data:
            # ck_release_record_target 要求 season/episode 二选一；聚合源
            # 未绑定 season（或未取种的源级前置拒绝）没有落库载体 → 只计数。
            return
        infohash = torrent_info_hash(data)  # reject 只在有 hash 时才走到这里
        if await self._store.find_release_by_hash(infohash) is not None:
            return
        await self._store.create_release(
            ReleaseRecord(
                season_id=target_season_id,
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
            await add(candidate.data, save_path=self._save_path)
        except GatewayError as exc:
            # 409 Conflict = qB 判定同内容任务已存在(私有站私改种子 hash 不同
            # 但内容相同也触发):已有文件场景,下载目录里的副本由库外自动扫描
            # 命中订阅后归档,release 如实标 FAILED 指向该出口。
            reason = (
                "already in downloader (409 conflict); file will be picked up by library autoscan"
                if "409" in str(exc) or "Conflict" in str(exc)
                else f"gateway: {exc}"
            )
            logger.warning("gateway add failed for %s: %s", candidate.infohash, exc)
            await self._store.transition_release(
                record.id,
                ReleaseStatus.FAILED,
                now=now,
                decision=Decision.REJECTED,
                reason=reason,
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
