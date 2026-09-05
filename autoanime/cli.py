from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from autoanime.config import load_settings
from autoanime.memory.store import SqliteStorage


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
    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init-db":
        settings = load_settings()
        store = SqliteStorage(settings.database_url)
        await store.create_all()
        await store.close()
        print("database initialized")
        return 0
    print(f"{args.command}: not implemented yet")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
