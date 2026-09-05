from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from autoanime.config import load_settings
from autoanime.core.interfaces import ParseResult, RawName
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
    subparsers.add_parser("confirm", help="Resolve pending items (placeholder)")
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


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init-db":
        settings = load_settings()
        store = SqliteStorage(settings.database_url)
        await store.create_all()
        await store.close()
        print("database initialized")
        return 0
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
