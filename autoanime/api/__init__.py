"""``autoanime api serve`` 启动入口（M3 后端，E2）。

计划原文要求 ``autoanime/api serve`` 启动；因 E1 并行开发冻结
``autoanime/cli.py``，子命令挂在独立入口模块：``python -m autoanime.api serve``。
D16：v1 仅 FastAPI（APScheduler 由 E4 在同一进程 lifespan 接入）。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from autoanime.config import load_settings
from autoanime.web.app import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m autoanime.api",
        description="Serve the AutoAnime v2 API (FastAPI + SSE).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the HTTP API server (uvicorn)")
    serve.add_argument("--host", default=None, help="Bind host (default: settings.api_host)")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default: settings.api_port)")
    serve.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: CORS 放开 localhost:5173（settings.api_cors_dev_origins）",
    )
    serve.add_argument("--db", default=None, help="Override database URL (sqlite+aiosqlite://...)")
    serve.add_argument("--toml", default=None, help="Path to autoanime.toml")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "serve":  # pragma: no cover - argparse 已约束
        print(f"unknown command: {args.command}")
        return 2
    settings = load_settings(Path(args.toml) if args.toml else None)
    if args.db:
        settings.database_url = args.db
    app = create_app(
        settings,
        cors_origins=settings.api_cors_dev_origins if args.dev else None,
    )
    uvicorn.run(
        app,
        host=args.host if args.host is not None else settings.api_host,
        port=args.port if args.port is not None else settings.api_port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

