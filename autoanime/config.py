from __future__ import annotations

import json
import logging
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import SecretStr, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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
    # TMDB v3 api_key（12-D 可写配置）：Settings 层默认 None（provider 内部
    # 回落读 env AUTOANIME_TMDB_API_KEY）；DB 覆盖后以 DB 为准。GET 永不
    # 回显，只给 has_tmdb_api_key 布尔（web/routers/settings.py）。
    tmdb_api_key: SecretStr | None = None
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

    # ------------------------------------------------------------------
    # 海报兜底下载（PR3+）：本地库无 poster 文件时从参考源拉图落盘
    # ------------------------------------------------------------------
    # 总开关（关闭 = 端点保持纯本地只读，行为与落库前一致）。
    poster_download_enabled: bool = True
    # 负缓存冷却天数：参考源拉图失败（无图/网络失败）后，冷却期内懒拉取
    # 直接 404，不再对同一目录反复外呼。
    poster_retry_cooldown_days: int = 7
    # 单张海报下载超时（秒）；图片 CDN 直链不走 API 配额但受此约束。
    poster_download_timeout_s: float = 15.0

    # ------------------------------------------------------------------
    # 订阅调度（E4 M4 闭环增量；ARCHITECTURE §2 + Plan §6 第 1/2 项）
    # ------------------------------------------------------------------
    # 调度总开关；关闭时 FastAPI 单独可用（python -m autoanime.api serve 语义不变）。
    scheduler_enabled: bool = True
    # RSS 轮询间隔（分钟，默认 30min）与抖动幅度（±10%：整点齐射打源站）。
    rss_poll_interval_minutes: int = 30
    rss_poll_jitter_pct: int = 10
    # 网络失败重试次数（指数退避后仍失败 → 跳过本轮，不 crash 不告警风暴）。
    rss_fetch_retries: int = 2
    # Mikan 主站部分地区被墙：RSS base_url 代理环境复用 HTTPS_PROXY；此处仅
    # 留超时。下载器轮询间隔（秒）与单任务失败重试上界（≤2）。
    rss_fetch_timeout_s: float = 30.0
    download_poll_interval_s: int = 30
    download_max_retries: int = 2
    # COLLECTED 降频检查周期（天，D15/ARCHITECTURE §1：每月仅洗版机会检查）。
    collected_check_days: int = 30
    # 待确认积压告警阈值（通知 D3：pending_queue 未决行数超过该值发提醒）。
    pending_backlog_alert_threshold: int = 20

    # ------------------------------------------------------------------
    # 下载网关（E4；qbittorrent 优先 / aria2 只接口 + 离线测试，拍板 D5）
    # ------------------------------------------------------------------
    downloader: str = "qbittorrent"  # qbittorrent | aria2
    qbittorrent_host: str = "127.0.0.1"
    qbittorrent_port: int = 8080
    qbittorrent_username: str = "admin"
    qbittorrent_password: SecretStr = SecretStr("")
    # 下载任务分类标记：补扫/进度轮询按 category 过滤，避免碰用户其他种子。
    qbittorrent_category: str = "autoanime"
    qbittorrent_timeout_s: float = 15.0
    # aria2 JSON-RPC（接口 + fake 测试，不实测）。
    aria2_rpc_url: str = "http://127.0.0.1:6800/jsonrpc"
    aria2_secret: SecretStr = SecretStr("")

    # ------------------------------------------------------------------
    # 洗版引擎（E4；ARCHITECTURE §3 + 拍板 D9/D21）
    # ------------------------------------------------------------------
    # 触发阈值：新候选 score ≥ 现有 score + upgrade_threshold（默认 2）。
    upgrade_threshold: float = 2.0
    # 单集洗版上限（episode.upgraded_count ≤ 2）。
    upgrade_max_per_episode: int = 2
    # 跨盘降级策略（D9）：allow=默认降级 copy；strict=永不 copy（跨盘跳过记 audit）。
    upgrade_copy_policy: str = "allow"
    # 单文件跳过阈值（GB，D9：>20GB 跳过记 audit）。
    upgrade_skip_size_gb: float = 20.0
    # 归档命名标题语言（D17）：title_cn → romaji 回退，Settings 可配。
    naming_title_language: str = "title_cn"
    # 错配隔离目录（D14 分支 B/C：救不动的文件移到这里等人工，不归档）。
    quarantine_path: Path = Path("./quarantine")

    # ------------------------------------------------------------------
    # 错配恢复（E4；拍板 D14 + Plan §6.1）
    # ------------------------------------------------------------------
    # 单集自动回补预算（默认 2 次，超限转人工；防错标源霸榜死循环烧流量）。
    mismatch_backfill_budget: int = 2

    # ------------------------------------------------------------------
    # 通知（E4；拍板 D3：webhook + telegram 最小版；密钥只走 env）
    # ------------------------------------------------------------------
    notify_enabled: bool = False
    # 通用 webhook（如企业微信/自建接收端）：POST JSON {category,message,payload}。
    notify_webhook_url: SecretStr | None = None
    # Telegram Bot：token + chat_id（BotFather 创建；SecretStr + env）。
    notify_telegram_bot_token: SecretStr | None = None
    notify_telegram_chat_id: str | None = None
    notify_timeout_s: float = 10.0
    # 事件订阅（可配置）：新集归档/缺集回补/洗版完成/待确认积压告警。
    notify_events: list[str] = [
        "episode.organized",
        "episode.gap",
        "upgrade.completed",
        "pending.backlog",
    ]

    model_config = SettingsConfigDict(env_prefix="AUTOANIME_", extra="ignore")


def load_settings(path: Path | None = None, overrides: Mapping[str, str] | None = None) -> Settings:
    """env/toml → DB 覆盖合并出运行时 Settings（12-D 配置中心）。

    优先级：toml → DB 覆盖（``overrides``）→ env。pydantic-settings 的
    语义是「显式 kwargs 优先于 env」，故 DB 覆盖作为 kwargs 传入时天然
    压过 env；未覆盖的字段仍回落 env/toml 默认。

    与 ``llm_api_key`` pop 的关系（12-D 设计点）：
    - toml 里的 ``llm_api_key`` 仍被 pop 忽略——v2「secrets 不进 toml」的
      纪律不变（toml 是可进版本库的文件）；
    - DB 覆盖在 pop **之后**合并，所以 WebUI 写入库的 ``llm_api_key`` /
      ``tmdb_api_key`` 会生效。DB 是本地 SQLite（与 ``.env`` 同级安全），
      明文可接受（docs/12 §风险 3），且 GET 永不回显、不进日志/audit。
    """
    config_path = path or Path("autoanime.toml")
    data: dict[str, Any] = {}
    if config_path.exists():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # Secrets are intentionally restricted to environment variables (or the
    # app_settings DB overrides applied below -- never the toml file).
    data.pop("llm_api_key", None)
    if overrides:
        data.update(parse_db_overrides(overrides))
    return Settings(**data)


def encode_setting_value(value: Any) -> str:
    """app_settings 覆盖项统一 JSON 序列化（bool/int/float/str/list[str]）。

    ``None`` 不该出现在这里：显式清除 = 删行（store.put_app_setting(None)），
    回落 env/toml 默认，而不是存一个 null 值。
    """
    return json.dumps(value, ensure_ascii=False)


def parse_db_overrides(rows: Mapping[str, str]) -> dict[str, Any]:
    """app_settings 行 → 字段值：按 Settings 注解逐项校验，非法行跳过。

    DB 里的 key 只可能来自 settings 路由 PUT 白名单，但为防手工改库/后续
    版本字段更名，未知 key 与类型不匹配的行记日志跳过——不让一条脏行
    拖垮启动。
    """
    out: dict[str, Any] = {}
    for key, raw in rows.items():
        field = Settings.model_fields.get(key)
        if field is None:
            logger.warning("app_settings: ignoring unknown key %r", key)
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        try:
            out[key] = TypeAdapter(field.annotation).validate_python(value)
        except ValidationError:
            logger.warning("app_settings: ignoring invalid value for %r", key)
    return out


def apply_db_overrides(settings: Settings, rows: Mapping[str, str]) -> Settings:
    """把 DB 覆盖合并进**已构造**的运行时 Settings（原地 setattr）。

    供 lifespan 用：storage 建好后读 ``app_settings`` 行，合并进
    create_app 持有的 Settings 实例（中间件/路由闭包引用同一对象，原地
    改才能让全进程可见）。优先级 env/toml → DB：DB 里的 key 都是用户在
    WebUI 显式写入的运行期项，视为最后意图，故覆盖 env。
    """
    for key, value in parse_db_overrides(rows).items():
        setattr(settings, key, value)
    return settings
