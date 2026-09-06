from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from autoanime.config import Settings, load_settings
from autoanime.core.enums import (
    Actor,
    Confidence,
    EpisodeState,
    MediaType,
    MemorySource,
    MemoryStatus,
    PendingStatus,
    SeasonState,
    Segment,
)
from autoanime.core.interfaces import LlmTransport, ParseResult, RawName, Registry
from autoanime.core.models import (
    AuditLog,
    Episode,
    ParseEvents,
    PendingQueue,
    RssSource,
    Season,
    Series,
)
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.store import SqliteStorage, StorageLlmCacheStore
from autoanime.organize import mover
from autoanime.organize.naming import NamingInput, relative_path
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import ROUTE_ARCHIVE, Orchestrator, RouteOutcome
from autoanime.providers import (
    LLM_TRANSPORT_NAME,
    register_providers,
    register_reference_providers,
)
from autoanime.scheduler.clock import SystemClock
from autoanime.scheduler.scheduler import build_loop
from autoanime.scheduler.store import LoopStore
from autoanime.web.learning import ACTION_PENDING_CONFIRM, pending_audit_row


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoanime",
        description="Local-first anime library automation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_help = (
        "Run one subscription-loop cycle now (startup reconcile + "
        "download poll + RSS poll; 与 rerun 共用同一实现，A7)"
    )
    subparsers.add_parser("run", help=run_help, description=run_help)
    confirm_parser = subparsers.add_parser(
        "confirm",
        help="Confirm a release's parse result and learn it into parse_memory",
    )
    confirm_parser.add_argument(
        "--name", required=True, help="Raw release name the confirmation refers to"
    )
    confirm_parser.add_argument(
        "--title", default=None, help="Confirmed title (defaults to the L1 draft of --name)"
    )
    confirm_parser.add_argument("--season", type=int, default=None, help="Confirmed season")
    confirm_parser.add_argument("--episode", type=int, default=None, help="Confirmed episode")
    confirm_parser.add_argument(
        "--segment",
        choices=sorted(segment.value for segment in Segment),
        default=None,
        help="Confirmed segment (defaults to the L1 draft, else 'episode')",
    )
    confirm_parser.add_argument(
        "--fansub", default=None, help="Confirmed fansub (defaults to the L1 draft)"
    )
    confirm_parser.add_argument(
        "--source",
        choices=sorted(source.value for source in MemorySource),
        default=MemorySource.MANUAL.value,
        help="Provenance of the confirmation",
    )
    report_parser = subparsers.add_parser(
        "report",
        help="Summarize stored parse_events + audit_log metrics",
    )
    report_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full summary as JSON (default: human-readable lines)",
    )
    subparsers.add_parser("init-db", help="Create the v2 SQLite schema")
    queue_help = (
        "List pending_queue rows awaiting manual confirmation "
        "(--status filter, --limit cap; table output, --json optional)"
    )
    queue_parser = subparsers.add_parser(
        "queue", help=queue_help, description=queue_help
    )
    queue_parser.add_argument(
        "--status",
        choices=sorted(status.value for status in PendingStatus),
        default=None,
        help="Only list rows in this status (default: all statuses)",
    )
    queue_parser.add_argument(
        "--limit", type=int, default=50, help="Maximum rows to show (default: 50)"
    )
    queue_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit rows as JSON (default: human-readable table)",
    )
    parse_parser = subparsers.add_parser(
        "parse",
        help=(
            "Parse a single release name through the L1/L2/L3 pipeline and "
            "the arbiter (JSON output)"
        ),
    )
    parse_parser.add_argument("--name", required=True, help="File name to parse")
    parse_parser.add_argument("--folder", default=None, help="Optional folder name")
    parse_parser.add_argument("--parent", default=None, help="Optional parent path")
    # --- E4：订阅闭环（M4） -----------------------------------------------------
    subscribe_parser = subparsers.add_parser(
        "subscribe",
        help=(
            "Subscribe a series: create Series/Season/Episode rows (air_date 判定"
            "一律 JST 展示转本地，D20) and optionally attach an RSS source"
        ),
    )
    subscribe_parser.add_argument("--title-cn", default=None, help="Chinese title")
    subscribe_parser.add_argument("--title-jp", default=None, help="Japanese title")
    subscribe_parser.add_argument("--title-romaji", default=None, help="Romaji title")
    subscribe_parser.add_argument("--season", type=int, default=1, help="Season number")
    subscribe_parser.add_argument(
        "--episodes", type=int, default=0, help="Pre-generate N MISSING episode rows"
    )
    subscribe_parser.add_argument(
        "--media-type",
        choices=sorted(t.value for t in MediaType),
        default=MediaType.TV.value,
        help="tv|movie|ova|special（Mikan 不支持 OVA/剧场版订阅，走散装导入）",
    )
    subscribe_parser.add_argument("--fansub", default=None, help="Preferred fansub group")
    subscribe_parser.add_argument(
        "--rss-url", default=None, help="RSS source URL (Mikan 私有订阅含 ?token= 的 URL)"
    )
    subscribe_parser.add_argument(
        "--rss-token", default=None, help="RSS token（密钥：只进库，不进日志/报告）"
    )
    rerun_parser = subparsers.add_parser(
        "rerun",
        help=(
            "Run one subscription-loop cycle now (download reconcile + poll; "
            "与调度器共用同一批 store 入口，A7)"
        ),
    )
    rerun_parser.add_argument(
        "--source-id", type=int, default=None, help="Only poll this rss_source id"
    )
    import_help = (
        "Scan a local directory for video files, route every release through "
        "the L1/L2/L3 pipeline (E1 batch entry, same-folder grouping) and "
        "archive or enqueue accordingly (JSON summary)"
    )
    import_parser = subparsers.add_parser(
        "import", help=import_help, description=import_help
    )
    import_parser.add_argument(
        "directory", help="Directory to scan recursively for video files"
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the actions that would be taken (with planned destinations) "
            "without writing the database or touching files"
        ),
    )
    return parser


async def _build_orchestrator(
    settings: Settings,
    *,
    metrics: bool = True,
) -> tuple[Orchestrator, SqliteStorage | None, object | None]:
    """Wire the full L1 -> L2 -> L3 -> arbiter pipeline.

    Every external capability degrades gracefully: the memory store, the LLM
    cache store and the arbiter audit sink all hang off the SQLite storage;
    the LLM transport comes from the provider registry (registered only when
    ``llm_enabled`` and the endpoint config are complete). The third element
    is the transport instance (if any) so the caller can release its HTTP
    client; an unusable storage degrades L2 and L3 caching together.
    """
    registry = Registry()
    registered = register_providers(registry, settings)
    transport_obj = registry.optional(LlmTransport, LLM_TRANSPORT_NAME) if registered else None
    llm_transport = cast("LlmTransport", transport_obj) if transport_obj is not None else None
    reference_chain = ReferenceChain(
        registry, order=settings.reference_order, enabled=settings.reference_enabled
    )
    l3_recognizer = LlmFallbackRecognizer.from_settings(settings)

    def _degraded_orchestrator() -> Orchestrator:
        # Storage unavailable: L2 and the L3 cache fall back together; the
        # orchestrator marks such passes degraded in its outcome.
        return Orchestrator(
            l2_enabled=settings.l2_enabled,
            l3_enabled=settings.llm_enabled,
            l3_recognizer=l3_recognizer,
            llm_transport=llm_transport,
            reference_chain=reference_chain,
        )

    if not settings.l2_enabled and not settings.llm_enabled:
        return Orchestrator(l2_enabled=False), None, None
    try:
        storage = SqliteStorage(settings.database_url)
    except Exception:
        return _degraded_orchestrator(), None, transport_obj
    try:
        await storage.create_all()
    except Exception:
        await storage.close()
        return _degraded_orchestrator(), None, transport_obj
    governance = MemoryGovernance(storage)
    # 参考源接线（PR6）：storage 就绪后带剧目级缓存与可选频控注册真实插件，
    # 再重建链（ReferenceChain 构造时解析 Registry）。降级路径沿用注册前的
    # 空链（lookup 恒 None，优雅降级），注册的 adapter 客户端为懒创建，
    # 一次性 CLI 进程结束即释放，无需显式 aclose。
    register_reference_providers(
        registry, cache_store=storage, reference_qps=settings.reference_qps
    )
    reference_chain = ReferenceChain(
        registry, order=settings.reference_order, enabled=settings.reference_enabled
    )
    return (
        Orchestrator(
            memory_store=StorageMemoryStore(storage, audit_governance=governance),
            l2_enabled=settings.l2_enabled,
            l3_enabled=settings.llm_enabled,
            l3_recognizer=l3_recognizer,
            llm_transport=llm_transport,
            llm_cache_store=StorageLlmCacheStore(storage),
            reference_chain=reference_chain,
            audit_sink=governance,
            # --dry-run 的「不落库」契约也约束指标旁路：parse_events 不写。
            metrics_sink=governance if metrics else None,
        ),
        storage,
        transport_obj,
    )


def _parse_result_to_json(result: ParseResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "title": result.title,
        "season": result.season,
        "episode": result.episode,
        "segment": result.segment.value,
        "fansub": result.fansub,
        "level": result.level.value,
        "confidence": result.confidence,
        "missing_fields": list(result.missing_fields),
        "evidence": dict(result.evidence),
    }


def _confirm_reference_lookup(
    settings: Settings, cache_store: SqliteStorage
) -> ReferenceChain | None:
    """The confirm-side reference lookup for alias backfill (PR7 M3/M2b).

    The same chain the parse pipeline uses: providers registered through the
    registry (each ``CachedReference``-wrapped over the confirm-side storage,
    so the backfill query shares the reference cache), ordered by
    ``reference_order``. ``reference_enabled=False`` yields ``None`` -- the
    backfill hook is simply not wired and confirm behaves byte-identically
    to the pre-M3 CLI.
    """
    if not settings.reference_enabled:
        return None
    registry = Registry()
    register_reference_providers(
        registry, cache_store=cache_store, reference_qps=settings.reference_qps
    )
    return ReferenceChain(
        registry, order=settings.reference_order, enabled=settings.reference_enabled
    )


async def _confirm(args: argparse.Namespace) -> int:
    draft = await LocalRecognizer().parse(RawName(name=args.name))
    title = args.title or (draft.title if draft else None)
    if not title:
        print("confirm: no confirmed title (L1 draft has none and --title not given)")
        return 2
    confirmed = ParseResult(
        title=title,
        season=args.season if args.season is not None else (draft.season if draft else None),
        episode=args.episode if args.episode is not None else (draft.episode if draft else None),
        segment=(
            Segment(args.segment)
            if args.segment is not None
            else (draft.segment if draft else Segment.EPISODE)
        ),
        fansub=args.fansub if args.fansub is not None else (draft.fansub if draft else None),
        # A user/LLM-confirmed result is by definition trusted input.
        level=Confidence.HIGH,
        confidence=1.0,
        missing_fields=(),
        evidence={},
    )
    settings = load_settings()
    async with SqliteStorage(settings.database_url) as storage:
        access = StorageMemoryAccess(storage)
        outcome = await learn_confirmation(
            access,
            confirmed=confirmed,
            raw_name=args.name,
            source=MemorySource(args.source),
            bypass_lookup=access,
            reference_lookup=_confirm_reference_lookup(settings, storage),
            draft_title=draft.title if draft else None,
        )
        # 确认收尾（与 WebUI confirm 同语义）：raw_name 匹配的未决 pending 行
        # 一并 resolve——否则 CLI 确认后队列不减，重跑 import 又被
        # already-pending 幂等挡住（第 4 轮真实测试发现）。
        resolved_count = await LoopStore(storage).resolve_open_pendings_by_raw_name(
            args.name,
            resolution={"action": "confirm", "confirmed_title": title},
            audit_row_for=lambda row: pending_audit_row(
                pending=row, action=ACTION_PENDING_CONFIRM, confirmed=confirmed
            ),
        )
    if outcome.bypassed:
        print(json.dumps({"bypassed": True, "entries": []}, ensure_ascii=False))
        return 0
    print(
        json.dumps(
            {
                "bypassed": False,
                "resolved_pending": resolved_count,
                "entries": [
                    {
                        "key_level": entry.key_level,
                        "key_hash": entry.key_hash,
                        "title_shape": entry.title_shape,
                        "source": MemorySource(entry.source).value,
                        "status": MemoryStatus(entry.status).value,
                        "hit_count": entry.hit_count,
                        "corrected_count": entry.corrected_count,
                    }
                    for entry in outcome.entries
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


async def _subscribe(args: argparse.Namespace) -> int:
    """CLI 订阅入口：与调度器/WEB 共用 LoopStore 收口（A7）。

    预生成 ``--episodes`` 条 MISSING 集行（air_date v1 由参考源回填，CLI 不
    写死假日期——缺集 diff 对无 air_date 的集「不硬拦截只降级」，
    ARCHITECTURE 5.6）。``--rss-token`` 是密钥：只落库，输出不回显。
    """
    if not (args.title_cn or args.title_jp or args.title_romaji):
        print("subscribe: need at least one of --title-cn/--title-jp/--title-romaji")
        return 2
    settings = load_settings()
    async with SqliteStorage(settings.database_url) as storage:
        store = LoopStore(storage)
        series = Series(
            title_cn=args.title_cn,
            title_jp=args.title_jp,
            title_romaji=args.title_romaji,
            media_type=MediaType(args.media_type),
            fansub_pref=args.fansub,
            status="active",
        )
        season = Season(number=args.season, status=SeasonState.AIRING)
        episodes = [
            Episode(number=number, state=EpisodeState.MISSING)
            for number in range(1, max(args.episodes, 0) + 1)
        ]
        created = await store.create_subscription(series, season, episodes)
        seasons = await store.seasons_for_series(created.id)
        season_id = seasons[0].id if seasons else None
        rss_id: int | None = None
        if args.rss_url and season_id is not None:
            saved = await store.add_rss_source(
                RssSource(
                    url=args.rss_url,
                    token=args.rss_token,
                    season_id=season_id,
                    enabled=True,
                )
            )
            rss_id = saved.id
        print(
            json.dumps(
                {
                    "series_id": created.id,
                    "season_id": season_id,
                    "episodes_pregenerated": len(episodes),
                    "rss_source_id": rss_id,
                    "rss_token_saved": args.rss_token is not None,
                },
                ensure_ascii=False,
            )
        )
    return 0


async def _run_loop_cycle(args: argparse.Namespace) -> int:
    """一轮订阅闭环（CLI run/rerun 共用实现，与调度器共用 poll 入口，A7）。

    顺序：启动补扫（悬挂任务）→ 下载比对 → RSS 轮询（--source-id 限定单
    源，仅 rerun 提供该参数）。输出 JSON 汇总；下载器不可达如实记 notes
    不视为失败。
    """
    settings = load_settings()
    components = build_loop(settings)
    try:
        await components.storage.create_all()
        reconcile = await components.download_poller.reconcile_startup(
            now=_now()
        )
        downloads = await components.download_poller.poll_once(now=_now())
        source_id: int | None = getattr(args, "source_id", None)
        if source_id is not None:
            source = await components.store.get_rss_source(source_id)
            if source is None:
                print(json.dumps({"error": f"rss source {source_id} not found"}))
                return 1
            outcome = await components.rss_poller.poll_source(source, now=_now())
            outcomes = [outcome]
            errors: list[str] = []
        else:
            report = await components.rss_poller.poll_all(now=_now())
            outcomes = list(report.outcomes)
            errors = list(report.errors)
        print(
            json.dumps(
                {
                    "reconciled": reconcile.reconciled,
                    "reconcile_notes": list(reconcile.notes),
                    "download": {
                        "checked": downloads.checked,
                        "completed": downloads.completed,
                        "failed": downloads.failed,
                        "retried": downloads.retried,
                        "notes": list(downloads.notes),
                    },
                    "rss": {
                        "sources": [
                            {
                                "source_id": o.source_id,
                                "season_id": o.season_id,
                                "skipped_not_due": o.skipped_not_due,
                                "fetch_error": o.fetch_error,
                                "entries_total": o.entries_total,
                                "seen": o.seen,
                                "rejected": o.rejected,
                                "backlog": o.backlog,
                                "picked": o.picked,
                                "gaps": list(o.gaps),
                            }
                            for o in outcomes
                        ],
                        "errors": errors,
                    },
                },
                ensure_ascii=False,
            )
        )
    finally:
        await components.close()
    return 0


def _now():
    return SystemClock().now()


def _aggregate_report(
    events: Sequence[Any], audits: Sequence[Any]
) -> dict[str, Any]:
    """Summarize stored ``parse_events`` + ``audit_log`` rows (pure aggregation).

    口径（Plan E1 第 3 项）：**人工介入率** = (audit 中 manual 纠正事件数)
    / (总归档事件数)。

    - manual 纠正事件 = ``audit_log`` 中 ``actor == manual`` 的行（E2 的
      /correct 端点落地后即写入该口径；actor 明细在 ``audit.by_actor``）；
    - 归档事件 = ``parse_events`` 中 ``outcome == "archive"`` 的行
      （orchestrator 每轮 parse 经 metrics_sink 写入）；分母为 0 时
      rate 返回 ``None`` 并注明；
    - v2 schema 的按日指标表是 ``parse_events``（event_date/level/
      llm_called/outcome/latency_ms）——Plan 文本的 "daily_metrics" 在库内
      以该表承载，此处如实按其聚合。
    """
    total_events = len(events)
    by_day: dict[Any, list[Any]] = {}
    outcome_counts: Counter[str] = Counter()
    llm_called = 0
    for event in events:
        by_day.setdefault(event.event_date, []).append(event)
        outcome_counts[str(event.outcome)] += 1
        if event.llm_called:
            llm_called += 1
    days = []
    for day in sorted(by_day):
        day_events = by_day[day]
        day_llm = sum(1 for event in day_events if event.llm_called)
        latencies = [event.latency_ms for event in day_events if event.latency_ms is not None]
        days.append(
            {
                "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
                "events": len(day_events),
                "llm_called": day_llm,
                "llm_call_rate": round(day_llm / len(day_events), 4) if day_events else 0.0,
                "by_level": dict(
                    sorted(Counter(str(event.level) for event in day_events).items())
                ),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 3)
                if latencies
                else None,
            }
        )
    audit_by_action = dict(sorted(Counter(str(row.action) for row in audits).items()))
    audit_by_actor = dict(sorted(Counter(str(row.actor) for row in audits).items()))
    manual_corrections = sum(1 for row in audits if row.actor == Actor.MANUAL)
    archived = outcome_counts.get(ROUTE_ARCHIVE, 0)
    return {
        "generated_from": {"parse_events": total_events, "audit_log": len(audits)},
        "parse_events": {
            "total": total_events,
            "days": days,
            "llm_called_total": llm_called,
            "llm_call_rate": round(llm_called / total_events, 4) if total_events else 0.0,
            "by_outcome": dict(sorted(outcome_counts.items())),
        },
        "audit": {
            "total": len(audits),
            "by_action": audit_by_action,
            "by_actor": audit_by_actor,
        },
        "manual_intervention_rate": {
            "manual_correction_events": manual_corrections,
            "archived_events": archived,
            "rate": round(manual_corrections / archived, 4) if archived else None,
            "note": (
                "manual_correction_events = audit_log.actor == manual 的行数；"
                "archived_events = parse_events.outcome == archive 的行数"
                "（orchestrator 每轮 parse 写入）；分母为 0 时 rate 为 null。"
            ),
        },
    }


def _render_report_text(report: dict[str, Any]) -> str:
    """Human-readable rendering of the aggregated report."""
    rate = report["manual_intervention_rate"]
    lines = [
        f"parse_events: {report['parse_events']['total']} events, "
        f"llm_call_rate {report['parse_events']['llm_call_rate']}",
        f"audit_log: {report['audit']['total']} rows "
        f"(by_actor {report['audit']['by_actor'] or '{}'})",
        (
            f"manual intervention rate: {rate['rate']} "
            f"({rate['manual_correction_events']} manual / {rate['archived_events']} archived)"
        ),
    ]
    return "\n".join(lines)


async def _report(args: argparse.Namespace) -> int:
    settings = load_settings()
    # 只读报表：不走 ``async with``（其 __aenter__ 会 create_all 建库），
    # 未初始化的库按错误路径给出 init-db 提示而非静默建库。
    storage = SqliteStorage(settings.database_url)
    try:
        events = await storage.list(ParseEvents)
        audits = await storage.list(AuditLog)
    except Exception as exc:  # noqa: BLE001 -- 未初始化/旧 schema 给出可操作提示
        print(f"report: storage unavailable ({type(exc).__name__}); run 'autoanime init-db'")
        return 1
    finally:
        await storage.close()
    payload = _aggregate_report(events, audits)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(_render_report_text(payload))
    return 0


def _pending_row_payload(row: PendingQueue) -> dict[str, object]:
    """pending_queue 行 → JSON 明细（与 E2 PendingOut 同字段口径）。"""
    return {
        "id": row.id,
        "raw_name": row.raw_name,
        "stage": row.stage,
        "reason": row.reason,
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "context": dict(row.context or {}),
        "created_at": (
            row.created_at.isoformat(timespec="seconds") if row.created_at else None
        ),
        "resolved_at": (
            row.resolved_at.isoformat(timespec="seconds") if row.resolved_at else None
        ),
        "resolution": row.resolution,
    }


def _render_pending_table(rows: Sequence[PendingQueue]) -> str:
    """Human-readable fixed-width table of pending_queue rows."""
    headers = ("id", "status", "stage", "raw_name", "reason", "created_at")
    cells = [
        (
            str(row.id),
            str(row.status.value if hasattr(row.status, "value") else row.status),
            row.stage,
            row.raw_name,
            row.reason or "",
            row.created_at.isoformat(timespec="seconds") if row.created_at else "",
        )
        for row in rows
    ]
    widths = [
        max([len(headers[i])] + [len(item[i]) for item in cells])
        for i in range(len(headers))
    ]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    for item in cells:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(item)))
    return "\n".join(lines)


async def _queue(args: argparse.Namespace) -> int:
    """CLI pending_queue 读侧：LoopStore 收口（A7），查询语义同 E2 分页。"""
    settings = load_settings()
    storage = SqliteStorage(settings.database_url)
    try:
        rows, total = await LoopStore(storage).list_pending(
            status=args.status, limit=max(args.limit, 0)
        )
    except Exception as exc:  # noqa: BLE001 -- 未初始化/旧 schema 给出可操作提示
        print(f"queue: storage unavailable ({type(exc).__name__}); run 'autoanime init-db'")
        return 1
    finally:
        await storage.close()
    if args.json:
        print(
            json.dumps(
                {
                    "total": total,
                    "count": len(rows),
                    "status": args.status,
                    "items": [_pending_row_payload(row) for row in rows],
                },
                ensure_ascii=False,
            )
        )
    else:
        scope = f" (status={args.status})" if args.status else ""
        print(f"pending_queue: showing {len(rows)} of {total} rows{scope}")
        if rows:
            print(_render_pending_table(rows))
    return 0


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# import：本地库存手动导入（用户头号验收场景：扫描 → 识别 → 归档/入队）
# ---------------------------------------------------------------------------

#: import 扫描的视频扩展名（v1 定版语义；naming.VIDEO_SUFFIXES 是归档域全集）。
_IMPORT_VIDEO_SUFFIXES = frozenset({".mkv", ".mp4", ".avi", ".ts"})

#: archive 路由里「给不出集号」的兜底：季包/无集 HIGH 结果不硬归档，入队人工。
_PENDING_REASON_NO_EPISODE = "archive route without episode number"


def _scan_video_files(root: Path) -> tuple[int, list[Path]]:
    """递归扫描目录下的视频文件；跳过隐藏（``.`` 开头）与临时（``~`` 开头）。

    返回 (目录内全部文件数, 选中的视频文件列表)；列表按路径排序，保证同目录
    分组的输入顺序稳定（合批分组与输出明细都可复现）。
    """
    total = 0
    videos: list[Path] = []
    for child in sorted(root.rglob("*")):
        if not child.is_file():
            continue
        total += 1
        if child.suffix.lower() not in _IMPORT_VIDEO_SUFFIXES:
            continue
        if child.name.startswith((".", "~")):
            continue
        videos.append(child)
    return total, videos


def _group_by_parent(files: Sequence[Path]) -> list[tuple[Path, list[Path]]]:
    """按父目录分组（E1 机会主义合批的 folder 语义）；目录按路径排序。"""
    groups: dict[Path, list[Path]] = {}
    for file in files:
        groups.setdefault(file.parent, []).append(file)
    return sorted(groups.items(), key=lambda item: item[0])


def _archive_plan(file: Path, result: ParseResult, settings: Settings) -> mover.TransferPlan:
    """L1 HIGH（route=archive）文件的归档计划：D17 命名 + D18 字幕跟随 + D9。

    复用 E4 organize 四件套中的 naming + mover（与 ArchiveService._archive_episode
    同一原语）；手动导入没有订阅行，解析结论的 title 即展示标题（三槽同填，
    由 ``naming_title_language`` 的回退链决定实际取用）。plan_transfer 是纯
    决策（只 stat 源文件），dry-run 亦可安全调用。
    """
    media_type = "movie" if result.segment is Segment.MOVIE else "tv"
    naming = NamingInput(
        title_cn=result.title,
        title_romaji=result.title,
        title_jp=result.title,
        season_number=result.season or 1,
        episode_number=result.episode or 0,
        media_type=media_type,
        release_title=file.name,
    )
    rel = relative_path(
        naming, language=settings.naming_title_language, extension=file.suffix.lower()
    )
    library_root = Path(settings.library_path)
    siblings = (
        [child for child in file.parent.iterdir() if child.is_file()]
        if file.parent.exists()
        else []
    )
    return mover.plan_transfer(
        file,
        library_root=library_root,
        dst_dir=library_root / rel.parent,
        dst_name=rel.name,
        siblings=siblings,
        copy_policy="strict" if settings.upgrade_copy_policy == "strict" else "allow",
        skip_over_bytes=int(settings.upgrade_skip_size_gb * 1024**3),
    )


def _pending_draft_context(file: Path, outcome: RouteOutcome) -> dict[str, object]:
    """pending 行 context：识别草稿契约键（web/learning._DRAFT_FIELDS 口径）。

    web 确认/纠正流（build_confirmed_result）直接消费这些键合成权威
    ParseResult；route/level 仅作人工处理时的现场信息。
    """
    result = outcome.result
    return {
        "title": result.title if result else None,
        "season": result.season if result else None,
        "episode": result.episode if result else None,
        "segment": result.segment.value if result else None,
        "fansub": result.fansub if result else None,
        "folder": file.parent.name,
        "parent_path": str(file.parent),
        "route": outcome.route,
        "level": result.level.value if result else None,
    }


def _pending_reason(outcome: RouteOutcome) -> str:
    """入队原因（与 E4 stage="mismatch" 行的 reason 同为人工可读短句）。"""
    result = outcome.result
    if result is None:
        return "unparsed"
    if outcome.route == ROUTE_ARCHIVE:
        return _PENDING_REASON_NO_EPISODE
    return f"{outcome.route}:{result.level.value}"


async def _import_audit(
    governance: MemoryGovernance,
    file: Path,
    result: ParseResult,
    plan: mover.TransferPlan,
    executed: mover.TransferResult,
) -> None:
    """import 归档的 audit 行（与 E4 归档同簿记：reverse 供回滚、E2 Logs 可见）。"""
    try:
        await governance.record_audit(
            operation_id=uuid4().hex,
            entity="episode",
            action="episode.organized",
            instruction={
                "file": file.name,
                "dst": str(executed.dst_paths[0]),
                "strategy": executed.strategy,
                "source": "import",
                "title": result.title,
                "season": result.season,
                "episode": result.episode,
            },
            reverse={"moves": list(plan.reverse_moves)},
        )
    except Exception:  # noqa: BLE001 -- 审计失败不阻塞归档（与 ArchiveService 同口径）
        logger.warning("import audit write failed", exc_info=True)


async def _handle_import_outcome(
    file: Path,
    outcome: RouteOutcome,
    *,
    settings: Settings,
    store: LoopStore,
    governance: MemoryGovernance,
    dry_run: bool,
) -> dict[str, object]:
    """单文件路由结果处理；返回该文件的输出明细（action 即发生的/将发生的动作）。

    - archive 路由且能给出演集号（或剧场版）→ organize 路径归档；
    - memory/l3 路由、LOW、无法解析、archive 但给不出集号 → 落 pending_queue
      （stage="import"，context 携带识别草稿契约键），供人工处理；
    - dry-run 只规划：归档给出计划目标位（plan_transfer 纯决策），入队不写行。
    """
    result = outcome.result
    item: dict[str, object] = {
        "file": str(file),
        "route": outcome.route,
        "title": result.title if result else None,
        "season": result.season if result else None,
        "episode": result.episode if result else None,
    }
    archivable = bool(
        result is not None
        and outcome.route == ROUTE_ARCHIVE
        and (result.segment is Segment.MOVIE or result.episode is not None)
    )
    if not archivable:
        item["action"] = "pending"
        item["reason"] = _pending_reason(outcome)
        if not dry_run:
            row = await store.add_pending(
                PendingQueue(
                    raw_name=file.name,
                    context=_pending_draft_context(file, outcome),
                    stage="import",
                    reason=str(item["reason"]),
                )
            )
            item["pending_id"] = row.id
        return item
    assert result is not None
    plan = _archive_plan(file, result, settings)
    item["strategy"] = plan.strategy
    item["dst"] = str(plan.dst_dir / plan.moves[0].dst_name) if plan.moves else None
    if plan.strategy == "skip":
        item["action"] = "skip"
        item["reason"] = plan.skip_reason
        return item
    # D21 守卫（R2 验收实测缺陷）：目标位已有文件时 import 不得静默覆盖——
    # 库内替换只能走 E4 洗版评分闸门（threshold/上限/audit upgrade.completed）。
    # 同一文件的重放已在前置幂等桶（already-archived）跳过；走到这里的目标位
    # 冲突如实跳过（同 inode = 内容已在库，不同 inode = 版本替换属洗版管辖）。
    if plan.moves:
        dst_path = plan.dst_dir / plan.moves[0].dst_name
        if dst_path.exists():
            try:
                same_content = dst_path.samefile(file)
            except OSError:
                same_content = False
            item["action"] = "skip"
            item["reason"] = "dst-exists-same-content" if same_content else "dst-exists-upgrade-gated"
            return item
    if dry_run:
        item["action"] = "archive"
        return item
    executed = await asyncio.to_thread(mover.execute_transfer, plan)
    if executed.error is not None or not executed.dst_paths:
        item["action"] = "error"
        item["reason"] = executed.error or "transfer failed"
        return item
    item["action"] = "archive"
    item["dst"] = str(executed.dst_paths[0])
    await _import_audit(governance, file, result, plan, executed)
    return item


async def _import(args: argparse.Namespace) -> int:
    """CLI 手动导入入口（``import <目录> [--dry-run]``）。

    递归扫描目录下视频文件，按父目录分组喂 ``orchestrator.process_batch
    (batching=True)``（E1 库存入口；expected=None——D13 手动路径）。--dry-run
    只输出将发生的动作不落库不归档。输出 JSON 汇总：
    total（目录内全部文件）/scanned（视频文件）/routes（路由分布）/
    archived/pending/failed（仅 error）/skipped（重跑幂等跳过与目标位
    已占用跳过——均非错误）/items（逐文件明细）。
    """
    root = Path(args.directory)
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {args.directory}"}, ensure_ascii=False))
        return 2
    settings = load_settings()
    total_seen, videos = _scan_video_files(root)
    orchestrator, storage, transport_obj = await _build_orchestrator(
        settings, metrics=not args.dry_run
    )
    own_storage = False
    if storage is None:
        # L1-only 配置（L2/L3 全关）：管线不带库，pending 落库自开一个。
        storage = SqliteStorage(settings.database_url)
        own_storage = True
    store = LoopStore(storage)
    governance = MemoryGovernance(storage)
    routes: Counter[str] = Counter()
    items: list[dict[str, object]] = []
    archived = pending = failed = skipped = 0
    if not args.dry_run:
        # 归档目标根先就位：plan_transfer 的同盘判定需要 stat 到 library 根，
        # 缺失时会把本可 hardlink 的归档静默降级为 copy（D9 hardlink 优先）。
        try:
            Path(settings.library_path).mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("library root not preparable: %s", settings.library_path)
    try:
        if own_storage:
            try:
                await storage.create_all()
            except Exception as exc:  # noqa: BLE001 -- 存储不可用给出可操作提示
                print(f"import: storage unavailable ({type(exc).__name__}); run 'autoanime init-db'")
                return 1
        # 重跑幂等（R2 验收）：已归档（audit episode.organized）或已在等人工
        # （未决 pending 行）的文件先于识别跳过——不重复 hardlink/audit/pending，
        # 也不再烧 LLM。key 都是源文件名（raw_name，与 audit instruction 同口径）。
        handled_names: dict[str, str] = {}
        for name in await store.archived_file_names():
            handled_names[name] = "already-archived"
        for name in await store.open_pending_raw_names():
            handled_names.setdefault(name, "already-pending")
        fresh: list[Path] = []
        for file in videos:
            reason = handled_names.get(file.name)
            if reason is None:
                fresh.append(file)
                continue
            skipped += 1
            items.append({"file": str(file), "route": None, "action": "skip", "reason": reason})
        for parent, files in _group_by_parent(fresh):
            raws = [
                RawName(name=file.name, folder=parent.name, parent_path=str(parent))
                for file in files
            ]
            try:
                outcomes = await orchestrator.process_batch(
                    raws,
                    batching=True,
                    batch_min_size=settings.batch_min_size,
                    batch_max_size=settings.batch_max_size,
                )
            except Exception as exc:  # noqa: BLE001 -- 单组失败不拖垮整批
                logger.warning("import batch failed for %s", parent, exc_info=True)
                for file in files:
                    items.append(
                        {"file": str(file), "route": None, "action": "error",
                         "reason": type(exc).__name__}
                    )
                    failed += 1
                continue
            for file, outcome in zip(files, outcomes, strict=True):
                routes[outcome.route] += 1
                item = await _handle_import_outcome(
                    file, outcome,
                    settings=settings, store=store, governance=governance,
                    dry_run=bool(args.dry_run),
                )
                action = str(item["action"])
                if action == "archive":
                    archived += 1
                elif action == "pending":
                    pending += 1
                elif action == "skip":
                    # 目标位已占用的跳过（同内容 noop / 让位洗版闸门）：
                    # 与幂等桶同属"未归档但非错误"，不进 failed。
                    skipped += 1
                else:  # error：归档动作异常终止
                    failed += 1
                items.append(item)
    finally:
        try:
            await cast("Any", transport_obj).aclose()
        except AttributeError:
            pass
        except Exception:
            pass
        if own_storage and storage is not None:
            await storage.close()
    print(
        json.dumps(
            {
                "directory": str(root),
                "dry_run": bool(args.dry_run),
                "total": total_seen,
                "scanned": len(videos),
                "routes": dict(sorted(routes.items())),
                "archived": archived,
                "pending": pending,
                "skipped": skipped,
                "failed": failed,
                "items": items,
            },
            ensure_ascii=False,
        )
    )
    return 0


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init-db":
        settings = load_settings()
        store = SqliteStorage(settings.database_url)
        await store.create_all()
        await store.close()
        print("database initialized")
        return 0
    if args.command == "confirm":
        return await _confirm(args)
    if args.command == "report":
        return await _report(args)
    if args.command == "queue":
        return await _queue(args)
    if args.command == "subscribe":
        return await _subscribe(args)
    if args.command == "rerun":
        return await _run_loop_cycle(args)
    if args.command == "run":
        return await _run_loop_cycle(args)
    if args.command == "import":
        return await _import(args)
    if args.command == "parse":
        settings = load_settings()
        orchestrator, storage, transport_obj = await _build_orchestrator(settings)
        try:
            outcome = await orchestrator.process(
                RawName(name=args.name, folder=args.folder, parent_path=args.parent)
            )
        finally:
            # The transport is created by providers.register_providers and is
            # only known here as an object; duck-type its optional aclose().
            try:
                await cast("Any", transport_obj).aclose()
            except AttributeError:
                pass
            except Exception:
                pass
            if storage is not None:
                await storage.close()
        payload = _parse_result_to_json(outcome.result)
        if payload is not None:
            payload["route"] = outcome.route
            payload["degraded"] = outcome.degraded
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    print(f"{args.command}: not implemented yet")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
