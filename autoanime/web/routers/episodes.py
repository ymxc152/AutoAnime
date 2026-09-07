"""Episodes 域（/api/episodes，12-F）：集重新识别两步端点。

12-F 风险条款：必须 dry-run 预览 + 确认两步，前端分两次调用——

- ``POST /{episode_id}/reparse`` body ``{"dry_run": true}``（默认）：走
  L1→L2→L3 全管线解析该集当前文件名（不移动文件、不写 parse_events），
  返回解析结果与将执行的归档动作预览（organize 同一套 naming + mover
  纯决策，dry-run 亦可安全调用）；
- ``dry_run=false``：实际执行——mover 计划 + 搬移（hardlink 优先，D21
  守卫沿用 plan_transfer 单一事实源），episode 状态/文件指针经
  LoopStore 状态机守卫落地，audit 与 organize 同口径
  （``episode.organized``，import 重跑幂等桶据此放行）。

错误语义：episode 不存在 404；源文件不在位（file_path 缺失/文件已不在
盘上）409；集状态不允许（再）归档 409；执行态用进程内互斥位防并发
（organize/run-once 现有 app.state 互斥位同款纪律）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from autoanime.config import Settings
from autoanime.core.enums import Actor, EpisodeState
from autoanime.core.events import EventCategory, InMemoryEventBus
from autoanime.core.interfaces import ParseContext, RawName
from autoanime.core.models import Episode, Season, Series
from autoanime.memory.governance import MemoryGovernance
from autoanime.organize import mover
from autoanime.organize.naming import NamingInput, relative_path
from autoanime.scheduler.store import LoopStore, TransitionError
from autoanime.web.deps import BusDep, GovernanceDep, SettingsDep, StorageDep
from autoanime.web.learning import publish
from autoanime.web.schemas import EpisodeReparseIn, EpisodeReparseOut

router = APIRouter(prefix="/episodes", tags=["episodes"])

logger = logging.getLogger(__name__)


def _state_of(row: Episode) -> EpisodeState:
    return EpisodeState(row.state.value if hasattr(row.state, "value") else row.state)


@router.post("/{episode_id}/reparse", response_model=EpisodeReparseOut)
async def reparse_episode(
    episode_id: int,
    body: EpisodeReparseIn,
    request: Request,
    settings: SettingsDep,
    storage: StorageDep,
    governance: GovernanceDep,
    bus: BusDep,
) -> EpisodeReparseOut:
    """集重新识别（12-F）：dry-run 预览解析结果与归档动作，确认后执行。"""
    store = LoopStore(storage)
    episode = await store.get_episode(episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"episode {episode_id} not found")
    context = await store.episode_context(episode_id)
    if context is None:
        raise HTTPException(
            status_code=409,
            detail=f"episode {episode_id} context (season/series) missing",
        )
    episode, season, series = context
    source = Path(episode.file_path) if episode.file_path else None
    if source is None or not source.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"episode {episode_id} source file not present: {episode.file_path!r}",
        )
    current_state = _state_of(episode)
    if not body.dry_run:
        # 执行态互斥（12-F）：与调度器/其他手动 reparse 并发的保护，
        # 参照 organize/run-once 的 app.state 互斥位纪律。
        if getattr(request.app.state, "episode_reparse_running", False):
            raise HTTPException(status_code=409, detail="another reparse is running")
        if (
            current_state is not EpisodeState.ORGANIZED
            and not current_state.can_transition(EpisodeState.ORGANIZED)
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"episode {episode_id} in state {current_state.value} "
                    "cannot be (re)organized"
                ),
            )
        request.app.state.episode_reparse_running = True
    try:
        return await _reparse(
            body=body,
            source=source,
            episode=episode,
            season=season,
            series=series,
            current_state=current_state,
            store=store,
            governance=governance,
            bus=bus,
            settings=settings,
        )
    finally:
        if not body.dry_run:
            request.app.state.episode_reparse_running = False


async def _reparse(
    *,
    body: EpisodeReparseIn,
    source: Path,
    episode: Episode,
    season: Season,
    series: Series,
    current_state: EpisodeState,
    store: LoopStore,
    governance: MemoryGovernance,
    bus: InMemoryEventBus,
    settings: Settings,
) -> EpisodeReparseOut:
    """两步主体（12-F）：全管线解析 + 归档动作预览/执行。"""
    # 延迟导入（同 pipeline 惯例：避免 web 聚合与 CLI 模块初始化成环）。
    from autoanime.cli import _build_orchestrator as build_full_orchestrator
    from autoanime.cli import _parse_result_to_json

    orchestrator, orch_storage, transport = await build_full_orchestrator(
        settings, metrics=not body.dry_run
    )
    try:
        outcome = await orchestrator.process(
            RawName(name=source.name, parent_path=str(source.parent)),
            ParseContext(known_series=series.id, fansub_pref=series.fansub_pref),
        )
        parsed = _parse_result_to_json(outcome.result)
        # 归档目标（organize 同一套 naming 原语：series/season 权威标题，
        # 与 ArchiveService._archive_episode 同源，不用解析草稿标题）。
        library_root = Path(settings.library_path)
        naming = NamingInput(
            title_cn=series.title_cn,
            title_romaji=series.title_romaji,
            title_jp=series.title_jp,
            season_number=season.number,
            episode_number=episode.number,
            media_type=(
                series.media_type.value
                if hasattr(series.media_type, "value")
                else str(series.media_type)
            ),
            release_title=source.name,
        )
        rel = relative_path(
            naming,
            language=settings.naming_title_language,
            extension=source.suffix.lower(),
        )
        dst_dir = library_root / rel.parent
        siblings = (
            [child for child in source.parent.iterdir() if child.is_file()]
            if source.parent.exists()
            else []
        )
        # plan_transfer 是纯决策（D21 目标位守卫在计划内，dry-run 安全）。
        plan = await asyncio.to_thread(
            mover.plan_transfer,
            source,
            library_root=library_root,
            dst_dir=dst_dir,
            dst_name=rel.name,
            siblings=siblings,
            copy_policy=(
                "strict" if settings.upgrade_copy_policy == "strict" else "allow"
            ),
            skip_over_bytes=int(settings.upgrade_skip_size_gb * 1024**3),
        )
        action: dict[str, object] = {
            "dst": str(dst_dir / plan.moves[0].dst_name) if plan.moves else None,
            "strategy": plan.strategy,
            "episode_state": current_state.value,
        }
        if plan.strategy == "skip":
            # D21 守卫命中（同内容幂等 / 洗版闸门管辖 / 超限等），如实透出。
            action["action"] = "skip"
            action["reason"] = plan.skip_reason
        elif body.dry_run:
            # 预览：只报告将执行的归档动作，不移动文件。
            action["action"] = "archive"
        else:
            executed = await asyncio.to_thread(mover.execute_transfer, plan)
            if executed.error is not None or not executed.dst_paths:
                raise HTTPException(
                    status_code=500,
                    detail=f"reparse transfer failed: {executed.error}",
                )
            result = outcome.result
            # 重新识别不改画质结论：只更新状态/文件指针，quality_score 保留。
            try:
                await store.update_episode_archive_state(
                    episode.id,
                    target=EpisodeState.ORGANIZED,
                    file_path=str(executed.dst_paths[0]),
                )
            except TransitionError as exc:  # pragma: no cover - 前置已守卫
                raise HTTPException(status_code=409, detail=str(exc)) from None
            action["action"] = "archive"
            action["dst"] = str(executed.dst_paths[0])
            action["strategy"] = executed.strategy
            try:
                await governance.record_audit(
                    operation_id=uuid4().hex,
                    entity="episode",
                    entity_id=episode.id,
                    action="episode.organized",
                    instruction={
                        "file": source.name,
                        "dst": str(executed.dst_paths[0]),
                        "strategy": executed.strategy,
                        "source": "reparse",
                        "title": result.title if result else None,
                        "season": result.season if result else None,
                        "episode": result.episode if result else None,
                    },
                    reverse={"moves": list(plan.reverse_moves)},
                    actor=Actor.MANUAL,
                )
            except Exception:  # noqa: BLE001 — 审计失败不阻塞归档
                logger.warning("reparse audit write failed", exc_info=True)
            try:
                await publish(
                    bus,
                    category=EventCategory.ORGANIZE,
                    message="episode.reparsed",
                    episode_id=episode.id,
                    dst=str(executed.dst_paths[0]),
                )
            except Exception:  # noqa: BLE001 — 事件/通知永不致命
                logger.warning("reparse event publish failed", exc_info=True)
        return EpisodeReparseOut(
            episode_id=episode.id,
            dry_run=body.dry_run,
            parsed=parsed,
            action=action,
        )
    finally:
        # transport 由 register_providers 创建，cli 只以 object 形态返回；
        # duck-type 其可选 aclose()（与 pipeline._run_import_task 同口径）。
        transport_any: Any = transport
        try:
            await transport_any.aclose()
        except AttributeError:
            pass
        except Exception:
            pass
        if orch_storage is not None:
            await orch_storage.close()


__all__ = ["router"]
