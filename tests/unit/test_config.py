from __future__ import annotations

from pathlib import Path

from autoanime.config import load_settings


def test_l2_enabled_defaults_to_true() -> None:
    assert load_settings(Path("does-not-exist.toml")).l2_enabled is True


def test_l2_enabled_reads_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text("l2_enabled = false\n", encoding="utf-8")

    assert load_settings(path).l2_enabled is False


def test_l3_fields_defaults() -> None:
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


def test_llm_api_key_stays_out_of_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text('llm_api_key = "sk-should-be-ignored"\n', encoding="utf-8")

    settings = load_settings(path)

    assert settings.llm_api_key is None
