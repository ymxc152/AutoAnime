from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./autoanime.db"
    library_path: Path = Path("./library")
    download_path: Path = Path("./downloads")
    dry_run: bool = True
    l2_enabled: bool = True
    log_level: str = "INFO"
    llm_api_key: SecretStr | None = None

    model_config = SettingsConfigDict(env_prefix="AUTOANIME_", extra="ignore")


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or Path("autoanime.toml")
    data: dict[str, Any] = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # Secrets are intentionally restricted to environment variables.
    data.pop("llm_api_key", None)
    return Settings(**data)
