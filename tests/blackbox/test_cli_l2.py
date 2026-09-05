"""Blackbox tests for the CLI L2 flywheel: parse -> confirm -> parse (PR4 T5)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2]
_DB_ENV = "AUTOANIME_DATABASE_URL"

# L1 parses this release to a MEDIUM result with season/fansub missing,
# so a confirmation of those fields must fuse on the next parse.
_SAMPLE = "Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv"


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "autoanime.cli", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )


def _env(db_path: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env[_DB_ENV] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    env.update(extra)
    return env


def _json(result: subprocess.CompletedProcess[str]) -> Any:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_parse_confirm_reparse_memory_flywheel(tmp_path: Path) -> None:
    env = _env(tmp_path / "cli.db")

    first = _json(_run(["parse", "--name", _SAMPLE], env))
    assert first["level"] == "medium"
    assert first["season"] is None
    assert "key_level" not in first["evidence"]

    confirmed = _json(_run(["confirm", "--name", _SAMPLE, "--season", "2", "--fansub", "MWeb"], env))
    assert confirmed["bypassed"] is False
    assert len(confirmed["entries"]) == 2
    assert all(entry["status"] == "active" for entry in confirmed["entries"])

    second = _json(_run(["parse", "--name", _SAMPLE], env))
    assert second["season"] == 2
    assert second["fansub"] == "MWeb"
    assert second["level"] == "high"
    assert second["confidence"] == 1.0
    assert second["evidence"]["season"] == "memory"
    assert second["evidence"]["fansub"] == "memory"
    assert second["evidence"]["key_level"] == "memory:1"
    # L1-decided fields are untouched by memory.
    assert second["title"] == first["title"]
    assert second["episode"] == first["episode"]
    assert second["evidence"]["title"] == first["evidence"]["title"]
    assert second["evidence"]["episode"] == first["evidence"]["episode"]


def test_reparse_increments_memory_hit_counter(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "cli.db"
    env = _env(db_path)
    _run(["parse", "--name", _SAMPLE], env)
    _run(["confirm", "--name", _SAMPLE, "--season", "2", "--fansub", "MWeb"], env)
    _json(_run(["parse", "--name", _SAMPLE], env))

    with sqlite3.connect(db_path) as connection:
        hits = list(connection.execute("SELECT hit_count FROM parse_memory WHERE key_level = 1"))
    assert hits and hits[0][0] >= 1


def test_l2_disabled_by_env_keeps_parse_on_the_l1_path(tmp_path: Path) -> None:
    env = _env(tmp_path / "cli.db", AUTOANIME_L2_ENABLED="0")

    first = _json(_run(["parse", "--name", _SAMPLE], env))
    assert first["level"] == "medium"
    # confirm keeps learning (T2 behaviour), but parse no longer consults memory.
    _json(_run(["confirm", "--name", _SAMPLE, "--season", "2", "--fansub", "MWeb"], env))
    second = _json(_run(["parse", "--name", _SAMPLE], env))

    assert second == first
    assert "key_level" not in second["evidence"]


def test_unavailable_storage_degrades_to_l1_without_crashing(tmp_path: Path) -> None:
    # The parent directory of the database does not exist: the store cannot
    # open, and parse must fall back to the plain L1 result with exit code 0.
    env = _env(tmp_path / "missing_dir" / "cli.db")

    payload = _json(_run(["parse", "--name", _SAMPLE], env))
    assert payload["title"] == "Anime AzurLane Slow Ahead"
    assert payload["level"] == "medium"
    assert "key_level" not in payload["evidence"]
