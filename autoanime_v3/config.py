from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AppConfig:
    database_path: Path
    alias_file: Path
    min_confidence: float = 0.86
    output_root: Optional[Path] = None
    operation_dir: Optional[Path] = None
    mode: str = "link"
    openai_enabled: bool = False
    openai_base_url: str = "https://api.openai.com"
    openai_model: str = "gpt-4.1-mini"
    openai_api_key: str = ""
    openai_timeout: int = 30

    @property
    def cache_path(self) -> Path:
        """兼容早期 v3 测试代码；新代码统一使用 database_path。"""
        return self.database_path


def _get_bool(parser: configparser.ConfigParser, key: str, default: bool) -> bool:
    try:
        return parser.getboolean("autoanime", key)
    except (ValueError, configparser.Error):
        return default


def load_config(config_path: Optional[Path], project_root: Path) -> AppConfig:
    parser = configparser.ConfigParser()
    if config_path and config_path.is_file():
        parser.read(str(config_path), encoding="utf-8")
    section = parser["autoanime"] if parser.has_section("autoanime") else {}
    path_base = config_path.parent if config_path else project_root

    def value(name: str, default: str = "") -> str:
        return str(section.get(name, default)).strip()

    def local_path(raw: str) -> Path:
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_absolute() else (path_base / candidate).resolve()

    state_dir = project_root / ".autoanime-v3"
    database_raw = value("database_path", value("cache_path", str(state_dir / "library.sqlite3")))
    aliases_raw = value("alias_file", str(project_root / "autoanime_v3" / "data" / "aliases.json"))
    output_raw = value("output_root", "")
    operation_raw = value("operation_dir", str(state_dir / "operations"))
    api_env = value("openai_api_key_env", "OPENAI_API_KEY")
    try:
        confidence = float(value("min_confidence", "0.86"))
    except ValueError:
        confidence = 0.86
    try:
        timeout = int(value("openai_timeout", "30"))
    except ValueError:
        timeout = 30
    return AppConfig(
        database_path=local_path(database_raw),
        alias_file=local_path(aliases_raw),
        min_confidence=max(0.0, min(1.0, confidence)),
        output_root=local_path(output_raw) if output_raw else None,
        operation_dir=local_path(operation_raw) if operation_raw else None,
        mode=value("mode", "link").lower(),
        openai_enabled=_get_bool(parser, "openai_enabled", False),
        openai_base_url=value("openai_base_url", "https://api.openai.com"),
        openai_model=value("openai_model", "gpt-4.1-mini"),
        openai_api_key=os.environ.get(api_env, "") or value("openai_api_key", ""),
        openai_timeout=max(5, timeout),
    )
