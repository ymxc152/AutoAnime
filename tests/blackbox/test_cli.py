from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[2]


def _run_cli(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "autoanime.cli", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )


def test_cli_help() -> None:
    result = _run_cli(["--help"])
    assert result.returncode == 0
    assert "run" in result.stdout
    assert "init-db" in result.stdout


def test_cli_init_db(tmp_path: Path) -> None:
    db_path = tmp_path / "cli.db"
    env = os.environ.copy()
    env["AUTOANIME_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    result = _run_cli(["init-db"], env=env)
    assert result.returncode == 0

    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "series" in tables
    assert "parse_events" in tables
