"""cli subscribe/rerun 单测（E4a）：库操作收口 + 密钥不回显。

全部为同步测试（CLI main() 内部 asyncio.run，不能再套运行中的事件循环）；
DB 侧校验走 sqlite3 同步读取。
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
    return path


def _run_cli(*args: str, db_path: Path | None = None) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def test_subscribe_creates_rows_and_rss_source(db_path: Path) -> None:
    code, out, _err = _run_cli(
        "subscribe",
        "--title-cn", "孤独摇滚",
        "--title-jp", "ぼっち・ざ・ろっく!",
        "--season", "1",
        "--episodes", "12",
        "--fansub", "LoliHouse",
        "--rss-url", "https://mikanani.me/RSS/MyBangumi",
        "--rss-token", "s3cret-token",
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["episodes_pregenerated"] == 12
    assert payload["rss_source_id"] is not None
    assert payload["rss_token_saved"] is True
    assert "s3cret-token" not in out  # 密钥不回显
    with sqlite3.connect(db_path) as conn:
        series = conn.execute("SELECT title_cn, fansub_pref FROM series").fetchall()
        episodes = conn.execute("SELECT state FROM episode").fetchall()
        rss = conn.execute("SELECT token, enabled FROM rss_sources").fetchall()
    assert series == [("孤独摇滚", "LoliHouse")]
    assert len(episodes) == 12
    assert all(row[0] == "missing" for row in episodes)
    assert rss == [("s3cret-token", 1)]


def test_subscribe_requires_a_title(db_path: Path) -> None:
    code, _out, _err = _run_cli("subscribe", "--episodes", "2")
    assert code == 2


def test_rerun_end_to_end_offline(db_path: Path) -> None:
    """订阅后立刻手动触发一轮（空源 + 无在途下载 = 纯离线路径，A7 共用入口）。"""
    code1, out1, _err = _run_cli(
        "subscribe", "--title-cn", "孤独摇滚", "--episodes", "2",
        "--rss-url", "https://mikanani.me/RSS/MyBangumi?token=x",
    )
    assert code1 == 0
    code2, out2, _err = _run_cli("rerun")
    assert code2 == 0
    payload = json.loads(out2.splitlines()[-1])
    source = payload["rss"]["sources"][0]
    assert source["skipped_not_due"] is False
    assert source["entries_total"] == 0
    assert payload["rss"]["errors"] == []


def test_rerun_unknown_source_id_fails_cleanly(db_path: Path) -> None:
    _code, _out, _err = _run_cli("subscribe", "--title-cn", "孤独摇滚", "--episodes", "1")
    code, out, _err = _run_cli("rerun", "--source-id", "999")
    assert code == 1
    assert "not found" in out


def test_run_matches_rerun_offline(db_path: Path) -> None:
    """run 与 rerun 共用同一轮闭环实现（空源 + 无在途下载 = 纯离线路径）。"""
    code1, _out1, _err = _run_cli(
        "subscribe", "--title-cn", "孤独摇滚", "--episodes", "1",
        "--rss-url", "https://mikanani.me/RSS/MyBangumi?token=x",
    )
    assert code1 == 0
    code2, out2, _err = _run_cli("run")
    assert code2 == 0
    payload = json.loads(out2.splitlines()[-1])
    assert payload["rss"]["errors"] == []
    assert payload["download"]["checked"] == 0
    assert payload["reconciled"] == 0
    code3, out3, _err = _run_cli("rerun")
    assert code3 == 0
    payload3 = json.loads(out3.splitlines()[-1])
    assert set(payload) == set(payload3)  # 同一实现的同一输出形状


def test_run_help_carries_real_semantics(capsys: pytest.CaptureFixture[str]) -> None:
    """run --help 去掉 placeholder 字样，给出真实语义说明。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["run", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "placeholder" not in out
    assert "reconcile" in out
    assert "subscription-loop cycle" in out
