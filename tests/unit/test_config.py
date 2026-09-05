from __future__ import annotations

from pathlib import Path

from autoanime.config import load_settings


def test_l2_enabled_defaults_to_true() -> None:
    assert load_settings(Path("does-not-exist.toml")).l2_enabled is True


def test_l2_enabled_reads_toml(tmp_path: Path) -> None:
    path = tmp_path / "autoanime.toml"
    path.write_text("l2_enabled = false\n", encoding="utf-8")

    assert load_settings(path).l2_enabled is False
