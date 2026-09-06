"""cli queue 单测（收尾接线）：LoopStore 读侧 + 表格/JSON 输出 + 状态过滤。

全部同步测试（CLI main() 内部 asyncio.run，不能再套运行中的事件循环）；
pending_queue 行用 sqlite3 直插（与 E2 查询语义解耦的固定夹具）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from autoanime.cli import main


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cli.db"
    monkeypatch.setenv("AUTOANIME_DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    code, _out, _err = _run_cli("init-db")
    assert code == 0
    return path


def _run_cli(*args: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def _insert_pending(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """直插 pending_queue 行：(raw_name, stage, reason, status)。"""
    with sqlite3.connect(db_path) as conn:
        for raw_name, stage, reason, status in rows:
            conn.execute(
                "INSERT INTO pending_queue (raw_name, context, stage, reason, status,"
                " created_at) VALUES (?, '{}', ?, ?, ?, ?)",
                (raw_name, stage, reason, status, "2026-09-06 12:00:00.000000"),
            )
        conn.commit()


def test_queue_lists_all_rows_as_table(db_path: Path) -> None:
    _insert_pending(
        db_path,
        [
            ("Frieren - 01.mkv", "import", "l3:medium", "pending"),
            ("Movie Batch B.mkv", "mismatch", "branch C: over budget", "pending"),
            ("Old Resolved.mkv", "import", "l3:low", "resolved"),
        ],
    )
    code, out, _err = _run_cli("queue")
    assert code == 0
    assert "showing 3 of 3 rows" in out
    assert "Frieren - 01.mkv" in out
    assert "branch C: over budget" in out
    assert "import" in out


def test_queue_json_output_carries_row_fields(db_path: Path) -> None:
    _insert_pending(db_path, [("Frieren - 01.mkv", "import", "l3:medium", "pending")])
    code, out, _err = _run_cli("queue", "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["status"] is None
    (item,) = payload["items"]
    assert item["raw_name"] == "Frieren - 01.mkv"
    assert item["stage"] == "import"
    assert item["reason"] == "l3:medium"
    assert item["status"] == "pending"
    assert item["context"] == {}
    assert item["resolved_at"] is None


def test_queue_status_filter(db_path: Path) -> None:
    _insert_pending(
        db_path,
        [
            ("A.mkv", "import", "l3:medium", "pending"),
            ("B.mkv", "import", "l3:low", "pending"),
            ("C.mkv", "import", "l3:low", "skipped"),
        ],
    )
    code, out, _err = _run_cli("queue", "--status", "pending")
    assert code == 0
    assert "showing 2 of 2 rows" in out
    assert "A.mkv" in out and "B.mkv" in out
    assert "C.mkv" not in out


def test_queue_limit_keeps_newest_first(db_path: Path) -> None:
    _insert_pending(
        db_path,
        [
            ("A.mkv", "import", "r1", "pending"),
            ("B.mkv", "import", "r2", "pending"),
            ("C.mkv", "import", "r3", "pending"),
        ],
    )
    code, out, _err = _run_cli("queue", "--limit", "1")
    assert code == 0
    assert "showing 1 of 3 rows" in out
    # id 倒序（E2 分页语义）：最新一行在前。
    assert "C.mkv" in out
    assert "A.mkv" not in out


def test_queue_empty_db_renders_header_only(db_path: Path) -> None:
    code, out, _err = _run_cli("queue")
    assert code == 0
    assert "showing 0 of 0 rows" in out


def test_queue_uninitialized_db_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AUTOANIME_DATABASE_URL",
        f"sqlite+aiosqlite:///{(tmp_path / 'missing.db').as_posix()}",
    )
    code, out, _err = _run_cli("queue")
    assert code == 1
    assert "storage unavailable" in out
    assert "init-db" in out
