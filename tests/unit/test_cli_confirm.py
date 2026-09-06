"""CLI confirm 的 pending 收尾语义（第 4 轮真实测试发现）。

CLI confirm 原本只学习不 resolve pending 行——与 WebUI
``POST /pending/{id}/confirm``（确认即 resolve）不一致：CLI 用户确认后
队列永远挂着，重跑 import 又被 already-pending 幂等挡住。修复后 confirm
按 raw_name 匹配未决行一并 resolve（resolved_by=manual + 同事务审计行）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from autoanime.cli import main

PENDING_NAME = "Frieren - 01.mkv"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """隔离环境：tmp 库 + tmp 媒体库 + LLM/参考源关闭。"""
    db = tmp_path / "cli.db"
    library = tmp_path / "library"
    monkeypatch.setenv("AUTOANIME_DATABASE_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    monkeypatch.setenv("AUTOANIME_LIBRARY_PATH", library.as_posix())
    monkeypatch.setenv("AUTOANIME_LLM_ENABLED", "false")
    monkeypatch.setenv("AUTOANIME_REFERENCE_ENABLED", "false")
    return {"db": db, "library": library, "root": tmp_path}


def _run_cli(*args: str) -> tuple[int, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue()


def _pending_rows(db: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT raw_name, status, resolution, resolved_by FROM pending_queue ORDER BY id"
        ).fetchall()


def _pending_audit_rows(db: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT action, actor FROM audit_log WHERE action = 'pending_confirm'"
        ).fetchall()


def test_confirm_resolves_matching_pending_row(env: dict[str, Path]) -> None:
    """confirm 学习后按 raw_name resolve 未决 pending 行（与 WebUI 同语义）。"""
    downloads = env["root"] / "downloads"
    downloads.mkdir()
    (downloads / PENDING_NAME).write_bytes(b"x")

    code, out = _run_cli("import", downloads.as_posix())
    assert code == 0
    rows = _pending_rows(env["db"])
    assert len(rows) == 1 and rows[0][1] == "pending"

    code, out = _run_cli(
        "confirm",
        "--name", PENDING_NAME,
        "--title", "葬送的芙莉莲",
        "--season", "1",
        "--episode", "1",
        "--source", "manual",
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["bypassed"] is False
    assert payload["resolved_pending"] == 1

    rows = _pending_rows(env["db"])
    assert rows[0][1] == "resolved"
    assert rows[0][2] is not None and "葬送的芙莉莲" in str(rows[0][2])
    assert rows[0][3] == "manual"

    # 确认归档通路：确认结果直接 hardlink 入库（D17 命名），不再需要
    # 「确认后删除重导」（报告 §6.1 v2 首要补齐项）
    # 确认结果无画质信息 → 命名 quality 槽取默认 SD（naming 契约降级）
    archived = env["library"] / "葬送的芙莉莲" / "Season 01" / "葬送的芙莉莲 - S01E01.SD.mkv"
    assert archived.exists()
    src = env["root"] / "downloads" / PENDING_NAME
    assert src.exists()  # D21：下载原件保留（hardlink 归档）

    # 审计行（manual_intervention_rate 的数据源）同步落库
    audits = _pending_audit_rows(env["db"])
    assert audits == [("pending_confirm", "manual")]


def test_confirm_then_reimport_reenters_pipeline_with_learned_title(
    env: dict[str, Path],
) -> None:
    """确认后重导：幂等桶不再挡该文件，新 pending 草稿用确认名（学习生效）。"""
    downloads = env["root"] / "downloads"
    downloads.mkdir()
    (downloads / PENDING_NAME).write_bytes(b"x")
    _run_cli("import", downloads.as_posix())
    _run_cli(
        "confirm",
        "--name", PENDING_NAME,
        "--title", "葬送的芙莉莲",
        "--season", "1",
        "--episode", "1",
        "--source", "manual",
    )

    code, out = _run_cli("import", downloads.as_posix())
    assert code == 0
    payload = json.loads(out)
    # 确认已归档：重导被 already-archived 幂等桶放行（audit 同口径）
    item = payload["items"][0]
    assert item["action"] == "skip"
    assert item["reason"] == "already-archived"
    assert payload["skipped"] == 1 and payload["failed"] == 0
