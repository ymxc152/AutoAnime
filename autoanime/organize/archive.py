"""归档服务（E4b）：下载完成回调 → 对齐 → 归档/洗版/错配恢复 全链路。

D13/D14/§6.1 的执行域。下载轮询器确认完成 → 本服务接管：

1. 以 ``release_record(episode_id, torrent_hash)`` 组装 per-file
   ``expected``（权威载体，D13）；
2. 逐视频文件走 orchestrator（``expected`` 接线：对齐一致 → HIGH 快路径，
   跳过 L2/API）；
3. 快路径 + 期望集状态分流：DOWNLOADED → 首次归档；ORGANIZED → 洗版替换；
4. 非快路径按解析结论分流：解析出同番其他集且该集 MISSING → 改挂归档
   （A 语义，零重下）；否则 ``decide_mismatch``（A/B/C）执行：
   B 隔离 + pending_queue；C 隔离 + 集回 MISSING + 立即缺口通知（D15：
   v1 回补 = 等 RSS 自然命中）+ hash 拉黑（bypass 登记）；
5. 归档/洗版经 mover（hardlink 优先 / copy 降级 / 超限跳过，D9/D21），
   audit 行带 reverse instruction（5.4，与 E2 rollback 端点共用语义）。

文件 IO 一律 ``asyncio.to_thread``；状态转移经 LoopStore 守卫；audit 与
事件失败不阻塞主流程。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from autoanime.config import Settings
from autoanime.core.enums import (
    Actor,
    Decision,
    EpisodeState,
)
from autoanime.core.events import Event, EventBus, EventCategory
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.core.models import Episode, PendingQueue, ReleaseRecord, Season, Series
from autoanime.memory.governance import MemoryGovernance
from autoanime.organize import mover
from autoanime.organize.expected import (
    ExpectedContext,
    MismatchEvidence,
    decide_mismatch,
    title_matches,
)
from autoanime.organize.naming import VIDEO_SUFFIXES, NamingInput, relative_path
from autoanime.organize.upgrade import decide_upgrade, score_from_title
from autoanime.scheduler.store import LoopStore, TransitionError

logger = logging.getLogger(__name__)


@dataclass
class ArchiveReport:
    """一次完成回调的处理小计（报表/测试用）。"""

    torrent_hash: str
    fast_path_hits: int = 0
    archived: int = 0
    upgraded: int = 0
    reattached: int = 0
    quarantined: int = 0
    backfilled: int = 0
    notes: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, object]:
        return {
            "torrent_hash": self.torrent_hash,
            "fast_path_hits": self.fast_path_hits,
            "archived": self.archived,
            "upgraded": self.upgraded,
            "reattached": self.reattached,
            "quarantined": self.quarantined,
            "backfilled": self.backfilled,
            "notes": self.notes,
        }


class ArchiveService:
    """完成回调的确定性执行域（不持内存状态，一切以库为准）。"""

    def __init__(
        self,
        store: LoopStore,
        orchestrator: Any,
        gateway: Any,
        *,
        settings: Settings,
        governance: MemoryGovernance | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._gateway = gateway
        self._settings = settings
        self._governance = governance
        self._bus = bus

    # ------------------------------------------------------------------ entry

    async def handle_completed(
        self, release: ReleaseRecord, files: list[dict[str, object]] | None = None
    ) -> ArchiveReport:
        """DownloadPoller 的 on_completed 钩子（签名见 CompletedCallback）。"""
        del files  # 文件清单以磁盘扫描为准（gateway.files 仅参考）
        report = ArchiveReport(torrent_hash=release.torrent_hash)
        if release.episode_id is None:
            report.notes.append("release has no episode target; skip organize")
            return report
        context_row = await self._store.episode_context(release.episode_id)
        if context_row is None:
            report.notes.append("episode/season/series context missing")
            return report
        episode, season, series = context_row
        content_dir = await self._content_dir(release)
        if content_dir is None:
            report.notes.append("downloader content path unavailable; organize pending")
            return report
        video_files = await asyncio.to_thread(self._scan_videos, content_dir)
        if not video_files:
            report.notes.append("no video file found in content dir")
            return report

        expected = ExpectedContext(
            series_id=series.id,
            season_number=season.number,
            episode_number=episode.number,
            title_cn=series.title_cn,
            title_jp=series.title_jp,
            title_romaji=series.title_romaji,
            fansub_pref=series.fansub_pref,
            torrent_hash=release.torrent_hash,
            release_record_id=release.id,
        )
        parse_context = ParseContext(known_series=series.id, fansub_pref=series.fansub_pref)

        for video in video_files:
            try:
                await self._process_file(
                    video, release=release, expected=expected,
                    season=season, series=series,
                    parse_context=parse_context, report=report,
                )
            except TransitionError as exc:
                logger.warning("transition refused during organize: %s", exc)
                report.notes.append(f"transition refused: {video.name}")
        return report

    # ------------------------------------------------------------ per-file

    async def _process_file(
        self,
        video: Path,
        *,
        release: ReleaseRecord,
        expected: ExpectedContext,
        season: Season,
        series: Series,
        parse_context: ParseContext,
        report: ArchiveReport,
    ) -> None:
        outcome = await self._orchestrator.process(
            RawName(name=video.name, parent_path=str(video.parent)),
            parse_context,
            expected=expected,
        )
        result = outcome.result
        if getattr(outcome, "fast_path", False) and result is not None:
            report.fast_path_hits += 1
            await self._route_by_episode_state(
                video, release=release, expected=expected, season=season,
                series=series, result=result, report=report,
            )
            return
        # 非快路径：解析结论驱动的确定性分流
        title_match = bool(
            result is not None
            and title_matches(result.title, expected.titles())
        )
        season_match = bool(
            result is None or result.season is None or result.season == expected.season_number
        )
        target: Episode | None = None
        if (
            result is not None
            and title_match
            and season_match
            and result.episode is not None
        ):
            # season 上下文必须经 expected 集的 episode_id 取（R2/R3 验收修复：
            # episode_context 的参数是 episode id；expected.episode_number 是
            # 集号——多季订阅下 id≠number，传号会查到别的番的行）。
            context_row = (
                await self._store.episode_context(release.episode_id)
                if release.episode_id is not None
                else None
            )
            if context_row is not None:
                target = await self._store.episode_for_number(
                    context_row[1].id, result.episode
                )
        if target is not None and target.id != expected.episode_number and (
            self._state(target) is EpisodeState.MISSING
        ):
            # A 语义（文件级改挂）：同番其他集仍缺 → 直接归档到该集，零重下
            expected_episode_id = release.episode_id
            await self._archive_episode(
                video, release=release,
                episode=target, season=season, series=series,
                result=result, report=report,
            )
            await self._store.set_release_episode(release.id, target.id)
            report.reattached += 1
            await self._audit(
                operation_id=uuid4().hex,
                entity="episode",
                entity_id=target.id,
                action="mismatch.reattached",
                instruction={
                    "torrent_hash": release.torrent_hash,
                    "file": video.name,
                    "from_episode": expected.episode_number,
                    "to_episode": target.number,
                },
            )
            # 期望集解除占用（R3 验收实测缺陷）：其下载内容已改挂他集，
            # 原集若停留在 DOWNLOADED 是无文件谎报，且该状态会挡住回补
            # 重下——回 MISSING 等 RSS 自然命中（与 C 分支回缺同语义）。
            await self._release_expected_episode(expected_episode_id, season)
            return
        await self._handle_mismatch(
            video, release=release, expected=expected, season=season,
            series=series, result=result, title_match=title_match,
            target=target, report=report,
        )

    async def _release_expected_episode(self, episode_id: int | None, season: Season) -> None:
        """改挂后把原期望集回 MISSING（DOWNLOADED → MISSING 合法转移，D14）。

        仅在原集确处 DOWNLOADED（下载完成但内容已改挂他集）时回缺并发
        episode.gap；其余状态（MISSING/ORGANIZED 等）不动，转移被拒时记
        note 不 crash。
        """
        if episode_id is None:
            return
        context_row = await self._store.episode_context(episode_id)
        if context_row is None:
            return
        episode = context_row[0]
        if self._state(episode) is not EpisodeState.DOWNLOADED:
            return
        try:
            await self._store.transition_episode(episode.id, EpisodeState.MISSING)
        except TransitionError as exc:
            logger.warning("expected episode cannot return to MISSING: %s", exc)
            return
        await self._publish(
            EventCategory.NOTIFY,
            "episode.gap",
            {
                "season_id": season.id,
                "gap": [episode.number],
                "reason": "mismatch_reattach",
            },
        )

    async def _route_by_episode_state(
        self,
        video: Path,
        *,
        release: ReleaseRecord,
        expected: ExpectedContext,
        season: Season,
        series: Series,
        result: ParseResult,
        report: ArchiveReport,
    ) -> None:
        # 期望集状态经 release.episode_id 取行（episode_context 参数是 id，
        # expected.episode_number 是集号——多季订阅下 id≠number）。
        context_row = (
            await self._store.episode_context(release.episode_id)
            if release.episode_id is not None
            else None
        )
        if context_row is None:
            report.notes.append("expected episode context missing; nothing to organize")
            return
        episode = context_row[0]
        state = self._state(episode)
        if state is EpisodeState.DOWNLOADED:
            await self._archive_episode(
                video, release=release, episode=episode, season=season,
                series=series, result=result, report=report,
            )
        elif state is EpisodeState.ORGANIZED:
            await self._upgrade_episode(
                video, release=release, episode=episode, season=season,
                series=series, result=result, report=report,
            )
        else:
            report.notes.append(f"episode {episode.number} in state {state}; nothing to organize")

    # ------------------------------------------------------------ archive

    async def _archive_episode(
        self,
        video: Path,
        *,
        release: ReleaseRecord,
        episode: Episode,
        season: Season,
        series: Series,
        result: ParseResult | None,
        report: ArchiveReport,
    ) -> None:
        naming = NamingInput(
            title_cn=series.title_cn,
            title_romaji=series.title_romaji,
            title_jp=series.title_jp,
            season_number=season.number,
            episode_number=episode.number,
            media_type=series.media_type.value if hasattr(series.media_type, "value") else str(series.media_type),
            release_title=video.name,
        )
        rel = relative_path(naming, language=self._settings.naming_title_language)
        dst_dir = Path(self._settings.library_path) / rel.parent
        plan = await asyncio.to_thread(
            mover.plan_transfer,
            video,
            library_root=Path(self._settings.library_path),
            dst_dir=dst_dir,
            dst_name=rel.name,
            siblings=self._siblings(video),
            copy_policy=self._copy_policy(),
            skip_over_bytes=int(self._settings.upgrade_skip_size_gb * 1024**3),
        )
        if plan.strategy == "skip":
            report.notes.append(f"transfer skipped: {plan.skip_reason}")
            await self._audit_skip(video, release, plan.skip_reason or "skip")
            return
        executed = await asyncio.to_thread(mover.execute_transfer, plan)
        if executed.error is not None or not executed.dst_paths:
            report.notes.append(f"transfer failed: {executed.error}")
            await self._audit_skip(video, release, executed.error or "transfer failed")
            return
        score = float(release.score) if release.score is not None else score_from_title(
            video.name, fansub=result.fansub if result else None,
            fansub_pref=series.fansub_pref, seeders=None,
        )
        await self._store.update_episode_archive_state(
            episode.id,
            target=EpisodeState.ORGANIZED,
            file_path=str(executed.dst_paths[0]),
            quality_score=score,
        )
        report.archived += 1
        operation_id = uuid4().hex
        await self._audit(
            operation_id=operation_id,
            entity="episode",
            entity_id=episode.id,
            action="episode.organized",
            instruction={
                "file": video.name,
                "dst": str(executed.dst_paths[0]),
                "strategy": executed.strategy,
                "quality_score": score,
                "torrent_hash": release.torrent_hash,
            },
            reverse={"moves": list(plan.reverse_moves)},
        )
        await self._publish(
            EventCategory.ORGANIZE,
            "episode.organized",
            {"episode_id": episode.id, "path": str(executed.dst_paths[0]), "score": score},
        )

    async def _upgrade_episode(
        self,
        video: Path,
        *,
        release: ReleaseRecord,
        episode: Episode,
        season: Season,
        series: Series,
        result: ParseResult | None,
        report: ArchiveReport,
    ) -> None:
        """洗版替换（D9/D21）：hardlink 优先、旧种不动、失败保留旧文件。"""
        decision = decide_upgrade(
            candidate_score=float(release.score or 0.0),
            current_score=float(episode.quality_score or 0.0),
            upgraded_count=int(episode.upgraded_count or 0),
            threshold=self._settings.upgrade_threshold,
            max_upgrades=self._settings.upgrade_max_per_episode,
        )
        if not decision.allowed:
            report.notes.append(f"upgrade recheck refused: {decision.reason}")
            await self._store.set_release_decision(
                release.id, Decision.REJECTED,
                reason=f"upgrade recheck: {decision.reason}",
            )
            return
        naming = NamingInput(
            title_cn=series.title_cn,
            title_romaji=series.title_romaji,
            title_jp=series.title_jp,
            season_number=season.number,
            episode_number=episode.number,
            media_type=series.media_type.value if hasattr(series.media_type, "value") else str(series.media_type),
            release_title=video.name,
        )
        rel = relative_path(naming, language=self._settings.naming_title_language)
        dst_dir = Path(self._settings.library_path) / rel.parent
        plan = await asyncio.to_thread(
            mover.plan_transfer,
            video,
            library_root=Path(self._settings.library_path),
            dst_dir=dst_dir,
            dst_name=rel.name,
            siblings=self._siblings(video),
            copy_policy=self._copy_policy(),
            skip_over_bytes=int(self._settings.upgrade_skip_size_gb * 1024**3),
        )
        if plan.strategy == "skip":
            report.notes.append(f"upgrade skipped: {plan.skip_reason}")
            await self._audit_skip(video, release, plan.skip_reason or "skip")
            return
        old_path = Path(episode.file_path) if episode.file_path else None
        executed = await asyncio.to_thread(
            mover.replace_archive_file, old_path or Path(rel.name), plan,
        )
        if executed.error is not None or not executed.dst_paths:
            # 失败回滚：旧文件保留（mover 已清理新文件），release 记 rejected
            report.notes.append(f"upgrade failed: {executed.error}")
            await self._store.set_release_decision(
                release.id, Decision.REJECTED,
                reason=f"upgrade failed: {executed.error}",
            )
            await self._audit(
                operation_id=uuid4().hex,
                entity="episode",
                entity_id=episode.id,
                action="upgrade.rejected",
                instruction={
                    "file": video.name,
                    "reason": executed.error,
                    "torrent_hash": release.torrent_hash,
                },
            )
            return
        new_score = float(release.score or 0.0)
        await self._store.update_episode_archive_state(
            episode.id, target=EpisodeState.UPGRADED, upgraded_count_delta=1,
        )
        await self._store.update_episode_archive_state(
            episode.id,
            target=EpisodeState.ORGANIZED,
            file_path=str(executed.dst_paths[0]),
            quality_score=new_score,
        )
        report.upgraded += 1
        operation_id = uuid4().hex
        await self._audit(
            operation_id=operation_id,
            entity="episode",
            entity_id=episode.id,
            action="upgrade.completed",
            instruction={
                "file": video.name,
                "dst": str(executed.dst_paths[0]),
                "old_path": str(old_path) if old_path else None,
                "score": new_score,
                "torrent_hash": release.torrent_hash,
            },
            reverse={
                "replaced": {
                    "dst": str(old_path) if old_path else "",
                    "origin": str(old_path) if old_path else "",
                },
                "moves": list(plan.reverse_moves),
            },
        )
        await self._publish(
            EventCategory.ORGANIZE,
            "upgrade.completed",
            {
                "episode_id": episode.id,
                "path": str(executed.dst_paths[0]),
                "score": new_score,
            },
        )

    # ------------------------------------------------------------ mismatch

    async def _handle_mismatch(
        self,
        video: Path,
        *,
        release: ReleaseRecord,
        expected: ExpectedContext,
        season: Season,
        series: Series,
        result: ParseResult | None,
        title_match: bool,
        target: Episode | None,
        report: ArchiveReport,
    ) -> None:
        """错配恢复 A/B/C（先隔离 + rejected 落库，再按分支执行，D14）。"""
        # 回补预算按 expected 集的 episode_id 统计（R3 验收修复：该参数是
        # id；expected.episode_number 是集号——多季订阅下 id≠number，按号
        # 统计会数到别的番的 release，预算恒 0 / 误转人工）。
        expected_episode_id = release.episode_id
        rejected = [
            row
            for row in (
                await self._store.find_releases_by_episode(expected_episode_id)
                if expected_episode_id is not None
                else []
            )
            if self._decision(row) is Decision.REJECTED
        ]
        decision = decide_mismatch(
            MismatchEvidence(
                parse_valid=result is not None and result.episode is not None,
                title_match=title_match,
                target_episode_id=target.id if target is not None and self._state(target) is EpisodeState.MISSING else None,
                target_episode_state=self._state(target).value if target is not None else None,
                backfill_used=len(rejected),
                budget=self._settings.mismatch_backfill_budget,
            )
        )
        # 通用前置：release 记 rejected（先隔离 + rejected 落库，再诊断，D14）
        await self._store.set_release_decision(
            release.id, Decision.REJECTED,
            reason=f"mismatch {decision.branch}: {decision.detail}",
        )
        if decision.branch == "A_reattach" and decision.reattach_episode_id is not None:
            context_row = await self._store.episode_context(decision.reattach_episode_id)
            assert context_row is not None
            target_episode, target_season, target_series = context_row
            await self._store.set_release_episode(release.id, target_episode.id)
            await self._archive_episode(
                video, release=release, episode=target_episode,
                season=target_season, series=target_series,
                result=result, report=report,
            )
            report.reattached += 1
            await self._audit(
                operation_id=uuid4().hex,
                entity="episode",
                entity_id=target_episode.id,
                action="mismatch.reattached",
                instruction={
                    "torrent_hash": release.torrent_hash,
                    "file": video.name,
                    "branch": decision.branch,
                },
            )
            await self._release_expected_episode(expected_episode_id, season)
            return
        # B / C：隔离
        quarantine_dir = Path(self._settings.quarantine_path) / release.torrent_hash[:12]
        moved = await asyncio.to_thread(self._quarantine, video, quarantine_dir)
        report.quarantined += 1
        if decision.to_pending_queue:
            await self._store.add_pending(
                PendingQueue(
                    raw_name=video.name,
                    context={
                        "branch": decision.branch,
                        "detail": decision.detail,
                        "torrent_hash": release.torrent_hash,
                        "expected_episode": expected.episode_number,
                        "quarantine_path": str(moved) if moved else None,
                        "parse": {
                            "title": result.title if result else None,
                            "season": result.season if result else None,
                            "episode": result.episode if result else None,
                        },
                    },
                    stage="mismatch",
                    reason=decision.detail,
                )
            )
        if decision.blacklist_hash and release.torrent_hash:
            if self._governance is not None:
                await self._governance.add_bypass(
                    video.name, reason=f"mismatch {decision.branch}: {decision.detail}"
                )
        if decision.backfill:
            # C：期望集回 MISSING + 立即缺口通知（D15：回补等 RSS 自然命中）
            context_row = (
                await self._store.episode_context(expected_episode_id)
                if expected_episode_id is not None
                else None
            )
            if context_row is not None:
                missing_episode = context_row[0]
                try:
                    await self._store.transition_episode(
                        missing_episode.id, EpisodeState.MISSING
                    )
                    report.backfilled += 1
                except TransitionError:
                    report.notes.append("episode cannot return to MISSING")
            await self._publish(
                EventCategory.NOTIFY,
                "episode.gap",
                {
                    "season_id": season.id,
                    "gap": [expected.episode_number],
                    "reason": "mismatch_backfill",
                },
            )
        await self._audit(
            operation_id=uuid4().hex,
            entity="episode",
            entity_id=expected_episode_id,
            action="mismatch.quarantined",
            instruction={
                "branch": decision.branch,
                "detail": decision.detail,
                "file": video.name,
                "quarantine": str(moved) if moved else None,
                "torrent_hash": release.torrent_hash,
            },
        )
        await self._publish(
            EventCategory.ORGANIZE,
            "mismatch.quarantined",
            {"branch": decision.branch, "episode_id": expected_episode_id},
        )

    # ------------------------------------------------------------ helpers

    def _copy_policy(self) -> mover.CopyPolicy:
        return "strict" if self._settings.upgrade_copy_policy == "strict" else "allow"

    @staticmethod
    def _state(row: Episode) -> EpisodeState:
        return EpisodeState(row.state.value if hasattr(row.state, "value") else row.state)

    @staticmethod
    def _decision(row: ReleaseRecord) -> Decision:
        return Decision(row.decision.value if hasattr(row.decision, "value") else row.decision)

    @staticmethod
    def _scan_videos(content_dir: Path) -> list[Path]:
        if not content_dir.exists():
            return []
        found = [
            child
            for child in sorted(content_dir.rglob("*"))
            if child.is_file() and child.suffix.lower() in VIDEO_SUFFIXES
        ]
        return found

    @staticmethod
    def _siblings(video: Path) -> list[Path]:
        return (
            [child for child in video.parent.iterdir() if child.is_file()]
            if video.parent.exists()
            else []
        )

    @staticmethod
    def _quarantine(video: Path, quarantine_dir: Path) -> Path | None:
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            dst = quarantine_dir / video.name
            shutil.move(str(video), str(dst))
            return dst
        except OSError:
            return None

    async def _content_dir(self, release: ReleaseRecord) -> Path | None:
        try:
            status = await self._gateway.status(release.torrent_hash)
        except Exception:  # noqa: BLE001 — 网关不可达时退回下载根目录
            status = None
        if status:
            content = status.get("content_path") or status.get("save_path")
            if isinstance(content, str) and content:
                return Path(content)
        return None

    async def _season_id(self, expected: ExpectedContext) -> int:
        context_row = await self._store.episode_context(expected.episode_number)
        return context_row[1].id if context_row else 0

    async def _audit(self, *, operation_id: str, entity: str, entity_id: int | None,
                     action: str, instruction: dict[str, object],
                     reverse: dict[str, object] | None = None) -> None:
        if self._governance is None:
            return
        try:
            await self._governance.record_audit(
                operation_id=operation_id,
                entity=entity,
                entity_id=entity_id,
                action=action,
                instruction=instruction,
                reverse=reverse or {},
                actor=Actor.AUTO,
            )
        except Exception:  # noqa: BLE001 — 审计失败不阻塞归档
            logger.warning("archive audit write failed", exc_info=True)

    async def _audit_skip(self, video: Path, release: ReleaseRecord, reason: str) -> None:
        await self._audit(
            operation_id=uuid4().hex,
            entity="episode",
            entity_id=release.episode_id,
            action="organize.skipped",
            instruction={"file": video.name, "reason": reason,
                         "torrent_hash": release.torrent_hash},
        )

    async def _publish(self, category: EventCategory, message: str, payload: dict[str, object]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(Event(category=category, message=message, payload=payload))
        except Exception:  # noqa: BLE001
            logger.warning("event publish failed", exc_info=True)

