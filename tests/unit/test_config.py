from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoanime.config import load_settings


def test_l2_enabled_defaults_to_true() -> None:
    assert load_settings(Path("does-not-exist.toml")).l2_enabled is True


def test_l2_enabled_reads_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text("l2_enabled = false\n", encoding="utf-8")

    assert load_settings(path).l2_enabled is False


def test_l3_fields_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # 本机可能常驻 AUTOANIME_LLM_*（真源在 .env/shell）；默认值断言须隔离 env。
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    settings = load_settings(Path("does-not-exist.toml"))

    assert settings.llm_enabled is False
    assert settings.llm_model is None
    assert settings.llm_base_url is None
    assert settings.llm_timeout_s == 10.0
    assert settings.llm_max_retries == 2
    assert settings.llm_budget is None
    assert settings.reference_enabled is True
    assert settings.reference_order == ["bangumi", "tmdb"]
    assert settings.reference_qps is None


def test_batch_threshold_defaults() -> None:
    # E1 合批阈值契约（ARCHITECTURE 9.3b）：队列自然堆积 ≥5 才打包，上限 20。
    settings = load_settings(Path("does-not-exist.toml"))

    assert settings.batch_min_size == 5
    assert settings.batch_max_size == 20


def test_batch_threshold_reads_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text(
        "batch_min_size = 8\n"
        "batch_max_size = 32\n",
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.batch_min_size == 8
    assert settings.batch_max_size == 32


def test_l3_fields_read_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text(
        "llm_enabled = true\n"
        'llm_model = "test-model"\n'
        'llm_base_url = "https://example.invalid/v1"\n'
        "llm_timeout_s = 5.0\n"
        "llm_max_retries = 1\n"
        "llm_budget = 100\n"
        "reference_enabled = false\n"
        'reference_order = ["tmdb", "bangumi"]\n'
        "reference_qps = 0.5\n",
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.llm_enabled is True
    assert settings.llm_model == "test-model"
    assert settings.llm_base_url == "https://example.invalid/v1"
    assert settings.llm_timeout_s == 5.0
    assert settings.llm_max_retries == 1
    assert settings.llm_budget == 100
    assert settings.reference_enabled is False
    assert settings.reference_order == ["tmdb", "bangumi"]
    assert settings.reference_qps == 0.5


def test_llm_api_key_stays_out_of_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "autoanime.toml"
    path.write_text('llm_api_key = "sk-should-be-ignored"\n', encoding="utf-8")

    settings = load_settings(path)

    assert settings.llm_api_key is None


def test_api_section_defaults() -> None:
    settings = load_settings(Path("does-not-exist.toml"))

    # D6：默认空 token = 关闭认证。
    assert settings.api_token.get_secret_value() == ""
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.api_cors_dev_origins == ["http://localhost:5173"]
    assert settings.api_sse_heartbeat_s == 30.0
    assert settings.api_sse_replay_limit == 50


def test_api_token_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOANIME_API_TOKEN", "secret-token")

    settings = load_settings(Path("does-not-exist.toml"))

    assert settings.api_token.get_secret_value() == "secret-token"


def test_api_fields_read_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text(
        'api_host = "0.0.0.0"\n'
        "api_port = 9911\n"
        'api_cors_dev_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]\n'
        "api_sse_heartbeat_s = 15.0\n"
        "api_sse_replay_limit = 10\n",
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 9911
    assert settings.api_cors_dev_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    assert settings.api_sse_heartbeat_s == 15.0
    assert settings.api_sse_replay_limit == 10


# ---------------------------------------------------------------------------
# 12-D 配置中心：app_settings DB 覆盖合并（env/toml → DB）
# ---------------------------------------------------------------------------


def test_load_settings_applies_db_overrides() -> None:
    settings = load_settings(
        Path("does-not-exist.toml"),
        overrides={
            "llm_timeout_s": "5.0",
            "rss_poll_interval_minutes": "45",
            "reference_order": '["tmdb", "bangumi"]',
        },
    )

    assert settings.llm_timeout_s == 5.0
    assert settings.rss_poll_interval_minutes == 45
    assert settings.reference_order == ["tmdb", "bangumi"]


def test_load_settings_db_overrides_win_over_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text("llm_timeout_s = 5.0\n", encoding="utf-8")

    settings = load_settings(path, overrides={"llm_timeout_s": "7.5"})

    # 优先级：toml → DB 覆盖（DB 是用户在 WebUI 的最后意图）。
    assert settings.llm_timeout_s == 7.5


def test_load_settings_unoverridden_fields_still_read_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "autoanime.toml"
    # DB 覆盖以 kwargs 传入、env 优先级最低；本例 DB 未覆盖 llm_timeout_s，
    # 未被 kwargs/toml 提供的字段仍回落 env（pydantic-settings 语义不变）。
    monkeypatch.setenv("AUTOANIME_LLM_TIMEOUT_S", "9.0")

    settings = load_settings(path, overrides={"log_level": '"DEBUG"'})

    assert settings.llm_timeout_s == 9.0
    assert settings.log_level == "DEBUG"


def test_load_settings_db_can_set_llm_api_key_but_toml_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTOANIME_LLM_API_KEY", raising=False)
    path = tmp_path / "autoanime.toml"
    path.write_text('llm_api_key = "sk-should-be-ignored"\n', encoding="utf-8")

    # toml 里的 llm_api_key 仍被 pop 忽略（v2「secrets 不进 toml」不变）；
    # DB 覆盖在 pop 之后合并，WebUI 写入的密钥会生效（12-D 设计点）。
    settings = load_settings(path)
    assert settings.llm_api_key is None

    settings = load_settings(path, overrides={"llm_api_key": '"sk-from-db"'})
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "sk-from-db"


def test_parse_db_overrides_skips_unknown_and_invalid_keys() -> None:
    from autoanime.config import parse_db_overrides

    parsed = parse_db_overrides(
        {
            "llm_timeout_s": "5.0",
            "not_a_settings_field": '"x"',  # 未知 key
            "rss_poll_interval_minutes": '"not-an-int"',  # 类型不匹配
        }
    )

    assert parsed == {"llm_timeout_s": 5.0}


def test_apply_db_overrides_mutates_instance_in_place() -> None:
    from autoanime.config import apply_db_overrides

    settings = load_settings(Path("does-not-exist.toml"))
    merged = apply_db_overrides(settings, {"dry_run": "false"})

    assert merged is settings
    assert settings.dry_run is False


def test_encode_setting_value_roundtrip() -> None:
    from autoanime.config import encode_setting_value, parse_db_overrides

    for key, value in (
        ("dry_run", False),
        ("rss_poll_interval_minutes", 45),
        ("reference_qps", 0.5),
        ("notify_events", ["episode.organized", "upgrade.completed"]),
        ("llm_base_url", "https://example.invalid/v1"),
    ):
        assert parse_db_overrides({key: encode_setting_value(value)}) == {key: value}
