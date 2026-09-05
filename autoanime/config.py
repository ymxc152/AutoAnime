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
    llm_enabled: bool = False
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_timeout_s: float = 10.0
    llm_max_retries: int = 2
    llm_budget: int | None = None
    reference_enabled: bool = True
    reference_order: list[str] = ["bangumi", "tmdb"]
    # 缓存包装层的每 provider token bucket 速率（QPS）；None = 不启用包装层
    # 频控（P1 adapter 内部 HTTP 层已有默认 1 QPS 节流兜底）。
    reference_qps: float | None = None

    model_config = SettingsConfigDict(env_prefix="AUTOANIME_", extra="ignore")


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or Path("autoanime.toml")
    data: dict[str, Any] = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # Secrets are intentionally restricted to environment variables.
    data.pop("llm_api_key", None)
    return Settings(**data)
