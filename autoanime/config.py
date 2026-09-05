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
    # L3 机会主义合批阈值（ARCHITECTURE 9.3b，E1）：库存队列自然堆积
    # ≥ batch_min_size 个「同目录+同字幕组」文件才打包，单批上限
    # batch_max_size；订阅场景单文件快路径永不凑批（入口语义，非配置）。
    batch_min_size: int = 5
    batch_max_size: int = 20

    # ------------------------------------------------------------------
    # API / Web 服务（E2 M3 后端增量；独立 section，不动上方既有字段）
    # ------------------------------------------------------------------
    # 简单 token 认证（拍板 D6）：AUTOANIME_API_TOKEN 非空时校验
    # X-API-Token 头（SSE 另支持同值 query param，B7），空串 = 关闭认证。
    api_token: SecretStr = SecretStr("")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # --dev 模式下放开的 CORS 来源（Vite dev server 默认 5173）。
    api_cors_dev_origins: list[str] = ["http://localhost:5173"]
    # SSE：无消息心跳间隔（秒，注释帧防代理超时）与 Last-Event-ID 重放条数上限。
    api_sse_heartbeat_s: float = 30.0
    api_sse_replay_limit: int = 50

    model_config = SettingsConfigDict(env_prefix="AUTOANIME_", extra="ignore")


def load_settings(path: Path | None = None) -> Settings:
    config_path = path or Path("autoanime.toml")
    data: dict[str, Any] = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # Secrets are intentionally restricted to environment variables.
    data.pop("llm_api_key", None)
    return Settings(**data)
