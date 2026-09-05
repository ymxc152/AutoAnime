from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from typing import Any, cast

from autoanime.config import Settings, load_settings
from autoanime.core.enums import Confidence, MemorySource, MemoryStatus, Segment
from autoanime.core.interfaces import LlmTransport, ParseResult, RawName, Registry
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.lookup import StorageMemoryStore
from autoanime.memory.store import SqliteStorage, StorageLlmCacheStore
from autoanime.pipeline.l1_local import LocalRecognizer
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.pipeline.l3_llm import LlmFallbackRecognizer
from autoanime.pipeline.orchestrator import Orchestrator
from autoanime.providers import (
    LLM_TRANSPORT_NAME,
    register_providers,
    register_reference_providers,
)


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
    subparsers.add_parser("report", help="Emit pipeline metrics (placeholder)")
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
