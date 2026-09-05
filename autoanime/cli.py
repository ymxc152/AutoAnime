from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from autoanime.config import load_settings
from autoanime.core.enums import Confidence, MemorySource, MemoryStatus, Segment
from autoanime.core.interfaces import ParseResult, RawName
from autoanime.memory.learn import StorageMemoryAccess, learn_confirmation
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l1_local import LocalRecognizer


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
        help="Parse a single release name with the L1 pipeline (JSON output)",
    )
    parse_parser.add_argument("--name", required=True, help="File name to parse")
    parse_parser.add_argument("--folder", default=None, help="Optional folder name")
    parse_parser.add_argument("--parent", default=None, help="Optional parent path")
    return parser


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
        recognizer = LocalRecognizer()
        result = await recognizer.parse(
            RawName(name=args.name, folder=args.folder, parent_path=args.parent)
        )
        print(json.dumps(_parse_result_to_json(result), ensure_ascii=False))
        return 0
    print(f"{args.command}: not implemented yet")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
