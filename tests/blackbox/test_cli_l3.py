"""Blackbox tests for the CLI L3 full chain: parse -> L2 memory -> L3 -> arbiter.

Offline strategy:

- L3 成功路径：进程内直调 ``cli._dispatch``，monkeypatch CLI 装配的
  ``register_providers`` 注入脚本化 fake transport（无任何网络调用），
  覆盖 L2 miss → L3、L2 hit → L3 仲裁、L1-None → L3 采纳三条真实样本；
- 降级路径：子进程跑真实装配，LLM endpoint 指向本机不可达端口（连接
  立即被拒绝，无外网流量），parse 必须优雅回退 L1 结果。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from autoanime import cli
from autoanime.core.interfaces import LlmTransport, Registry
from tests.support.fixtures import load_case

_REPO_ROOT = Path(__file__).parents[2]
_DB_ENV = "AUTOANIME_DATABASE_URL"

# 真实样本 1：L1 MEDIUM，season/fansub 缺失 → L3 补齐并升档。
_SAMPLE_MISSING_SEASON = "Anime.AzurLane.Slow.Ahead.E03.1080p.Baha.WEB-DL.mkv"
# 真实样本 2：方言 F 目录级样本，L1 MEDIUM，season 缺失 → L3 补齐。
_SAMPLE_FOLDER_DERIVED = "尼古喵喵.EP03.简繁.1080p.H.264.AAC.SRTx2.mkv"
_SAMPLE_FOLDER = "[TV版&无修版] 尼古喵喵 - EP03 [简／繁] (1080p H.264 AAC SRTx2)"
# 真实样本 3：L1 完全无法解析 → L3 独立产出（采纳）。
_SAMPLE_UNPARSEABLE = "random_text_only"

_LLM_RESPONSE = json.dumps(
    {
        "title": "Anime AzurLane Slow Ahead",
        "season": 2,
        "episode": 3,
        "segment": "episode",
        "fansub": "MWeb",
    },
    ensure_ascii=False,
)


class FakeLlmTransport:
    """离线 fake transport：任何请求都返回预置 JSON 响应。"""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        self.calls += 1
        return self.response


def _dispatch(
    argv: list[str],
    *,
    transport: FakeLlmTransport | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any] | None:
    """Run one CLI dispatch in-process with L3 wired (fake transport)."""
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv(_DB_ENV, f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("AUTOANIME_LLM_ENABLED", "1")
    monkeypatch.setenv("AUTOANIME_LLM_MODEL", "test-model")
    monkeypatch.setenv("AUTOANIME_LLM_TIMEOUT_S", "2")
    monkeypatch.delenv("AUTOANIME_LLM_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # keep load_settings away from any repo toml

    if transport is not None:

        def fake_register(registry: Registry, settings: Any) -> bool:
            registry.register(LlmTransport, "openai")(transport)
            return True

        monkeypatch.setattr(cli, "register_providers", fake_register)

    args = cli._build_parser().parse_args(argv)
    exit_code = asyncio.run(cli._dispatch(args))
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


def test_parse_l3_fills_missing_fields_after_l2_miss(tmp_path, monkeypatch, capsys) -> None:
    transport = FakeLlmTransport(_LLM_RESPONSE)
    payload = _dispatch(
        ["parse", "--name", _SAMPLE_MISSING_SEASON],
        transport=transport,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert payload is not None
    assert payload["route"] == "l3"
    assert payload["degraded"] is False
    assert payload["title"] == "Anime AzurLane Slow Ahead"
    assert payload["season"] == 2
    assert payload["evidence"]["season"] == "llm"
    assert payload["evidence"]["fansub"] == "llm"
    assert payload["level"] == "high"  # R5 verified: L3 title matches L1 title shape
    assert transport.calls == 1  # cache miss -> exactly one real call


def test_parse_memory_hit_still_runs_l3_and_routes_memory(tmp_path, monkeypatch, capsys) -> None:
    transport = FakeLlmTransport(_LLM_RESPONSE)
    confirmed = _dispatch(
        ["confirm", "--name", _SAMPLE_MISSING_SEASON, "--season", "2", "--fansub", "MWeb"],
        transport=transport,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert confirmed is not None
    assert confirmed["bypassed"] is False

    payload = _dispatch(
        ["parse", "--name", _SAMPLE_MISSING_SEASON],
        transport=transport,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert payload is not None
    assert payload["route"] == "memory"
    assert payload["season"] == 2
    assert payload["evidence"]["season"] == "memory"  # L2 hit outranks LLM
    assert payload["fansub"] == "MWeb"
    assert transport.calls == 1  # confirm does not call LLM; the parse does


def test_parse_folder_derived_sample_goes_through_l3(tmp_path, monkeypatch, capsys) -> None:
    transport = FakeLlmTransport(_LLM_RESPONSE)
    payload = _dispatch(
        ["parse", "--name", _SAMPLE_FOLDER_DERIVED, "--folder", _SAMPLE_FOLDER],
        transport=transport,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert payload is not None
    # 方言 F 样本：L1 MEDIUM（season 缺失）→ L2 miss → L3 补齐 season。
    assert payload["route"] == "l3"
    assert payload["evidence"]["season"] == "llm"
    assert payload["season"] == 2
    assert payload["evidence"]["title"] == "name"  # L1 name evidence outranks LLM
    assert transport.calls == 1


def test_parse_l1_none_adopts_l3_result(tmp_path, monkeypatch, capsys) -> None:
    transport = FakeLlmTransport(_LLM_RESPONSE)
    payload = _dispatch(
        ["parse", "--name", _SAMPLE_UNPARSEABLE],
        transport=transport,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    assert payload is not None
    assert payload["route"] == "l3"
    assert payload["level"] == "medium"  # L1-None + LLM only: base MEDIUM
    assert set(payload["evidence"].values()) == {"llm"}
    assert transport.calls == 1


def test_arbiter_audit_rows_persisted_in_sqlite(tmp_path, monkeypatch, capsys) -> None:
    transport = FakeLlmTransport(_LLM_RESPONSE)
    _dispatch(
        ["parse", "--name", _SAMPLE_MISSING_SEASON],
        transport=transport,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
    )

    with sqlite3.connect(tmp_path / "cli.db") as connection:
        rows = connection.execute(
            "SELECT entity, action, operation_id, instruction FROM audit_log"
        ).fetchall()
    arbiter_rows = [row for row in rows if row[0] == "arbiter"]
    actions = {row[1] for row in arbiter_rows}
    assert "level_upgraded" in actions
    assert all(row[2] for row in arbiter_rows)  # operation_id batch non-empty


def test_cache_hit_on_second_parse_skips_transport(tmp_path, monkeypatch, capsys) -> None:
    transport = FakeLlmTransport(_LLM_RESPONSE)
    for _ in range(2):
        payload = _dispatch(
            ["parse", "--name", _SAMPLE_MISSING_SEASON],
            transport=transport,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            capsys=capsys,
        )
        assert payload is not None
        assert payload["season"] == 2
    assert transport.calls == 1  # second parse replayed the llm_cache row


def test_unroutable_llm_endpoint_degrades_to_l1(tmp_path: Path) -> None:
    """Subprocess + real wiring: LLM endpoint refused locally, no egress."""
    db_path = tmp_path / "cli.db"
    env = os.environ.copy()
    env[_DB_ENV] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    env["AUTOANIME_LLM_ENABLED"] = "1"
    env["AUTOANIME_LLM_MODEL"] = "test-model"
    env["AUTOANIME_LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
    env["AUTOANIME_LLM_TIMEOUT_S"] = "1"
    # 保证连接本机不可达端口不被环境代理劫持（无外网流量）。
    env["NO_PROXY"] = "*"
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    env.pop("ALL_PROXY", None)

    result = subprocess.run(
        [sys.executable, "-m", "autoanime.cli", "parse", "--name", _SAMPLE_MISSING_SEASON],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["route"] == "l3"
    assert payload["degraded"] is True
    assert payload["evidence"]["season"] == "none"  # L1 kept, LLM never won
    assert payload["season"] is None


def test_fixture_case_context_loader_still_works() -> None:
    # The in-process tests above hardcode their samples; this keeps the
    # fixture loader contract green for the sample used as sample 2's family.
    case = load_case(
        _REPO_ROOT / "tests" / "fixtures" / "samples" / "dialect_f" / "F01_tv_uncensored_noise"
    )
    assert case.expected is not None
    assert case.expected.missing_fields == ("season",)
