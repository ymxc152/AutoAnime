from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Sequence
from typing import Any, cast

from autoanime.config import Settings, load_settings
from autoanime.core.enums import (
    Actor,
    Confidence,
    EpisodeState,
    MediaType,
    MemorySource,
    MemoryStatus,
    SeasonState,
    Segment,
)
from autoanime.core.interfaces import LlmTransport, ParseResult, RawName, Registry
from autoanime.core.models import AuditLog, Episode, ParseEvents, RssSource, Season, Series
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.store import SqliteStorage, StorageLlmCacheStore
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import ROUTE_ARCHIVE, Orchestrator
from autoanime.providers import (
    LLM_TRANSPORT_NAME,
    register_providers,
    register_reference_providers,
)
from autoanime.scheduler.clock import SystemClock
from autoanime.scheduler.scheduler import build_loop
from autoanime.scheduler.store import LoopStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoanime",
        description="Local-first anime library automation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Process the download queue (placeholder)")
    subparsers.add_parser("import", help="Import a local library (placeholder)")
    subparsers.add_parser("queue", help="Inspect pending items (placeholder)")
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
    return parser


async def _build_orchestrator(
    settings: Settings,
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
        )
    if outcome.bypassed:
        print(json.dumps({"bypassed": True, "entries": []}, ensure_ascii=False))
        return 0
    print(
        json.dumps(
            {
                "bypassed": False,
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


async def _rerun(args: argparse.Namespace) -> int:
    """CLI 手动触发一轮订阅闭环（与调度器共用 poll 入口，A7）。

    顺序：启动补扫（悬挂任务）→ 下载比对 → RSS 轮询（--source-id 限定单
    源）。输出 JSON 汇总；下载器不可达如实记 notes 不视为失败。
    """
    settings = load_settings()
    components = build_loop(settings)
    try:
        await components.storage.create_all()
        reconcile = await components.download_poller.reconcile_startup(
            now=_now()
        )
        downloads = await components.download_poller.poll_once(now=_now())
        if args.source_id is not None:
            source = await components.store.get_rss_source(args.source_id)
            if source is None:
                print(json.dumps({"error": f"rss source {args.source_id} not found"}))
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
    - 归档事件 = ``parse_events`` 中 ``outcome == "archive"`` 的行（E4 的
      整理器落地后开始写入）；分母为 0 时 rate 返回 ``None`` 并注明；
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
                "（整理器在 M4 落地后写入）；分母为 0 时 rate 为 null。"
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
    if args.command == "subscribe":
        return await _subscribe(args)
    if args.command == "rerun":
        return await _rerun(args)
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
