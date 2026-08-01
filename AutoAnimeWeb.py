"""Run the AutoAnime LAN Web console."""

import argparse
from pathlib import Path

import uvicorn

from autoanime_v3.api.app import ServerSettings, create_app


def main(argv=None):
    parser = argparse.ArgumentParser(description="AutoAnime Web Console")
    parser.add_argument("--data-dir", type=Path, default=Path("C:/ProgramData/AutoAnime"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--insecure-http", action="store_true")
    args = parser.parse_args(argv)
    data_directory = args.data_dir.resolve()
    settings = ServerSettings(
        database_path=data_directory / "data" / "library.sqlite3",
        data_directory=data_directory,
        host=args.host,
        port=args.port,
        secure_cookies=not args.insecure_http,
        frontend_directory=Path(__file__).resolve().parent / "webui" / "dist",
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
