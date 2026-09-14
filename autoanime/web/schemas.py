"""Web 层 pydantic 请求/响应 schema（E2 M3 后端）。

只做参数校验与序列化组装；不承载业务规则。所有列表端点统一
``limit/offset`` 分页（``Page[T]`` 信封：total/limit/offset/items）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class Page[ItemT](BaseModel):
    """统一分页信封：total 为过滤条件下的总行数（PEP 695 泛型模型）。"""

    total: int
    limit: int
    offset: int
    items: list[ItemT]


# ---------------------------------------------------------------------------
# Library（/api/series）
# ---------------------------------------------------------------------------


class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    series_id: int
    season_id: int | None
    number: int
    state: str
    upgraded_count: int
    quality_score: float | None
    air_date: date | None
    file_path: str | None
    file_hash: str | None


class SeasonOut(BaseModel):
    id: int
    series_id: int
    number: int
    status: str
    episodes: list[EpisodeOut]


class SeriesOut(BaseModel):
    id: int
    title_cn: str | None
    title_jp: str | None
    title_romaji: str | None
    media_type: str
    tmdb_id: str | None
    bangumi_id: str | None
    fansub_pref: str | None
    quality_pref: str | None
    status: str
    seasons: list[SeasonOut]


# ---------------------------------------------------------------------------
# Pending（/api/pending）
# ---------------------------------------------------------------------------


class PendingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    raw_name: str
    context: dict[str, object]
    stage: str
    reason: str | None
    status: str
    resolution: dict[str, object] | str | None
    resolved_by: str | None
    created_at: datetime
    resolved_at: datetime | None

    @field_validator("resolution", mode="before")
    @classmethod
    def _parse_resolution_json(cls, value: object) -> object:
        """resolution 列为 String：合法 JSON 字符串解析为对象返回。"""
        if isinstance(value, str) and value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value


class PendingConfirmIn(BaseModel):
    """确认待确认项：字段缺省时回退到行内 context 草稿。"""

    title: str | None = None
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    segment: str | None = None
    fansub: str | None = None


class PendingCorrectIn(PendingConfirmIn):
    """字段纠正（5.2 学习三件套入口）：title 必填——纠正的核心是剧名归属。"""

    @model_validator(mode="after")
    def _title_required(self) -> PendingCorrectIn:
        if not self.title or not self.title.strip():
            raise ValueError("correct requires a non-empty 'title'")
        return self


class PendingResolveOut(BaseModel):
    id: int
    status: str
    resolution: dict[str, object] | None
    resolved_by: str
    learned_entries: int
    bypassed: bool


class PendingRejectIn(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Organize rollback（/api/organize/{id}/rollback）
# ---------------------------------------------------------------------------


class RollbackOut(BaseModel):
    audit_id: int
    operation_id: str
    applied: dict[str, object]
    learned: bool


# ---------------------------------------------------------------------------
# Audit（/api/audit）
# ---------------------------------------------------------------------------


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    operation_id: str
    entity: str
    entity_id: int | None
    action: str
    instruction: dict[str, object]
    reverse: dict[str, object]
    actor: str
    #: 行写入时刻（0008 迁移新增）；迁移前的历史行为 null。
    created_at: datetime | None = None


class OperationGroupOut(BaseModel):
    """按 operation_id 分组的 audit 汇总（Logs 页时间线展开用）。"""

    operation_id: str
    rows: int
    entities: list[str]
    actions: list[str]
    first_audit_id: int
    last_audit_id: int
    #: 组内 last_audit_id 行的写入时刻；历史行为 None。
    last_created_at: datetime | None = None
    #: 以组内最新行判定；UI 据此隐藏不可撤销操作，避免无意义 409。
    rollbackable: bool


# ---------------------------------------------------------------------------
# Subscriptions（/api/subscriptions）
# ---------------------------------------------------------------------------


class SeasonProgressOut(BaseModel):
    season_id: int
    number: int
    status: str
    episodes_total: int
    episodes_missing: int
    episodes_organized: int
    rss_sources: int


class SubscriptionOut(BaseModel):
    id: int
    title_cn: str | None
    title_jp: str | None
    title_romaji: str | None
    media_type: str
    status: str
    fansub_pref: str | None
    quality_pref: str | None
    include_keywords: str | None
    exclude_keywords: str | None
    seasons: list[SeasonProgressOut]
    # P0-B 一步订阅契约：token/URL 永不回显，只回「本次提交的 RSS 是否落库」。
    rss_saved: bool = False
    # P0-B adopt：创建请求命中已有 Series 被收编时为 True（重复订阅不静默）。
    adopted: bool = False


class SubscriptionCreateIn(BaseModel):
    title_cn: str | None = None
    title_jp: str | None = None
    title_romaji: str | None = None
    media_type: str = "tv"
    season_number: int = 1
    # 预生成当季集表（ARCHITECTURE §2）；None = 只建 Series/Season。
    episode_count: int | None = None
    fansub_pref: str | None = None
    quality_pref: str | None = None
    # 通用 RSS 规则：分号分隔关键词;include 非空 = 白名单,exclude 命中 = 拒绝
    include_keywords: str | None = None
    exclude_keywords: str | None = None
    # P0-B 一步订阅契约：bangumi_id 作 adopt 精确键；rss_url/rss_token 提供
    # 时与订阅同一事务挂 RSS 源（token 用 SecretStr 承载、任何响应不回显）。
    bangumi_id: str | None = None
    rss_url: str | None = None
    rss_token: SecretStr | None = None

    @field_validator("media_type")
    @classmethod
    def _media_type_known(cls, value: str) -> str:
        from autoanime.core.enums import MediaType

        if value not in {item.value for item in MediaType}:
            raise ValueError(f"unknown media_type: {value}")
        return value

    @field_validator("episode_count")
    @classmethod
    def _episode_count_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("episode_count must be a positive integer")
        return value

    @model_validator(mode="after")
    def _some_title(self) -> SubscriptionCreateIn:
        if not any(
            (self.title_cn, self.title_jp, self.title_romaji),
        ):
            raise ValueError("at least one of title_cn/title_jp/title_romaji is required")
        return self


class SubscriptionUpdateIn(BaseModel):
    status: Literal["active", "paused", "finished"] | None = None
    fansub_pref: str | None = None
    quality_pref: str | None = None
    include_keywords: str | None = None
    exclude_keywords: str | None = None

    @model_validator(mode="after")
    def _status_required_if_present(self) -> SubscriptionUpdateIn:
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status cannot be null")
        return self


# ---------------------------------------------------------------------------
# RSS sources（/api/rss_sources，B3；批次三聚合源增量）
# ---------------------------------------------------------------------------


class RssSourceOut(BaseModel):
    id: int
    url: str
    # token 永不回显（SecretStr 也不序列化明文，读取端点直接不返回）。
    has_token: bool
    # 聚合源（kind=aggregate）不绑季 → 可空；旧行/季绑定源恒有值。
    season_id: int | None
    enabled: bool
    last_polled_at: datetime | None
    kind: str = "season"
    include_keywords: str | None = None
    exclude_keywords: str | None = None


class RssSourceCreateIn(BaseModel):
    url: str
    token: SecretStr | None = None
    # kind=season（默认，季绑定）| aggregate（聚合源：一个 feed 混多部番，
    # 不绑季，轮询时对全部活跃订阅逐个对齐）。
    kind: Literal["season", "aggregate"] = "season"
    season_id: int | None = None
    enabled: bool = True
    # 聚合源级全局规则（分号分隔关键词，语义同 series 级；季绑定源忽略）。
    include_keywords: str | None = None
    exclude_keywords: str | None = None

    @field_validator("url")
    @classmethod
    def _url_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("url must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _season_required_for_season_kind(self) -> RssSourceCreateIn:
        # season 源必填 season_id；aggregate 源忽略 season_id（恒置空）。
        if self.kind == "season" and self.season_id is None:
            raise ValueError("season_id is required for season sources")
        if self.kind == "aggregate":
            self.season_id = None
        return self


class RssSourceUpdateIn(BaseModel):
    url: str | None = None
    token: SecretStr | None = None
    enabled: bool | None = None
    # 源级规则可更新；显式 null = 清除。kind 创建后只读（不提供修改）。
    include_keywords: str | None = None
    exclude_keywords: str | None = None


# ---------------------------------------------------------------------------
# Settings（/api/settings）
# ---------------------------------------------------------------------------


class SettingsOut(BaseModel):
    """运行时可见项（密钥一律不回显，只给 has_* 布尔）。

    重启生效档（requires_restart）字段在 DB 有覆盖时显示「重启后将生效
    的值」（12-D：这类字段 PUT 只落库不进运行时实例，GET 按待生效值
    展示，配合 PUT 响应的 requires_restart 提示）；其余字段即当前
    运行时值。
    """

    # --- 运行（识别/参考源/日志，v1 六项 + 12-D 立即生效档） ---
    dry_run: bool
    l2_enabled: bool
    llm_enabled: bool
    llm_model: str | None
    reference_enabled: bool
    reference_order: list[str]
    llm_timeout_s: float
    llm_max_retries: int
    reference_qps: float | None
    pending_backlog_alert_threshold: int
    log_level: str
    # --- 调度（scheduler_rebuild 档：PUT 后重建 loop 生效） ---
    scheduler_enabled: bool
    rss_poll_interval_minutes: int
    rss_poll_jitter_pct: int
    download_poll_interval_s: int
    download_max_retries: int
    collected_check_days: int
    # --- 下载器（requires_restart 档，密钥只回 has_*） ---
    downloader: str
    qbittorrent_host: str
    qbittorrent_port: int
    qbittorrent_username: str
    # --- 通知（requires_restart 档） ---
    notify_enabled: bool
    notify_telegram_chat_id: str | None
    notify_events: list[str]
    # --- 洗版 / 命名 / RSS 抓取（requires_restart 档） ---
    upgrade_threshold: float
    upgrade_max_per_episode: int
    upgrade_copy_policy: str
    upgrade_skip_size_gb: float
    mismatch_backfill_budget: int
    naming_title_language: str
    naming_movie_dir: bool
    naming_specials_s00: bool
    naming_year_suffix: bool
    rss_fetch_timeout_s: float
    rss_fetch_retries: int
    # --- 识别（requires_restart 档：LLM 连接类） ---
    llm_base_url: str | None
    # --- 环境（只读，v1 保持） ---
    library_path: str
    download_path: str
    #: 只读，resolve 后的绝对路径（相对路径展示不够定位时前端取用）。
    library_path_abs: str
    download_path_abs: str
    api_host: str
    api_port: int
    api_cors_dev_origins: list[str]
    api_sse_heartbeat_s: float
    api_sse_replay_limit: int
    # --- 密钥 has_* 布尔 ---
    has_api_token: bool
    has_llm_api_key: bool
    has_tmdb_api_key: bool
    has_qbittorrent_password: bool
    has_notify_webhook_url: bool
    has_notify_telegram_bot_token: bool
    # --- 并发写防护（乐观锁）：app_settings max(updated_at) 的 ISO 串；无覆盖行 = null ---
    updated_at: str | None = None


#: PUT 生效三档（12-D）：每个被改字段在响应里带档位，前端据此提示。
SettingEffect = Literal["immediate", "scheduler_rebuild", "requires_restart"]


class SettingsUpdateIn(BaseModel):
    """可写入的配置项（与路由白名单三档一致）。

    - 白名单外字段（extra="forbid"）→ 422，不再静默丢弃（12-D 收口：
      前端打错 key 应显式失败）；
    - 密钥字段（SecretStr）：空串 = 不修改，显式 null = 清除（RSS token
      惯例）；非密钥字段 null = 不修改（保持 v1 行为）；
    - ``base_updated_at``（并发写防护，非业务字段）：前端把 GET 拿到的
      ``updated_at`` 原样带回；提供且与当前不一致 → 409 settings_changed；
      缺省/null = 跳过检查（兼容 CLI/脚本）。
    """

    model_config = ConfigDict(extra="forbid")

    # 并发写防护（非业务字段，不进三档白名单；路由层先 pop 再处理）
    base_updated_at: str | None = None

    # 立即生效
    dry_run: bool | None = None
    l2_enabled: bool | None = None
    llm_enabled: bool | None = None
    llm_model: str | None = None
    reference_enabled: bool | None = None
    reference_order: list[str] | None = None
    llm_timeout_s: float | None = None
    llm_max_retries: int | None = None
    reference_qps: float | None = None
    pending_backlog_alert_threshold: int | None = None
    log_level: str | None = None
    # 调度类（重建 loop 生效）
    scheduler_enabled: bool | None = None
    rss_poll_interval_minutes: int | None = None
    rss_poll_jitter_pct: int | None = None
    download_poll_interval_s: int | None = None
    download_max_retries: int | None = None
    collected_check_days: int | None = None
    # 立即生效（路径类:绝对路径校验在路由层;需与下载器保存路径对齐）
    library_path: str | None = None
    download_path: str | None = None
    # 重启生效（连接/密钥类）
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    tmdb_api_key: SecretStr | None = None
    downloader: str | None = None
    qbittorrent_host: str | None = None
    qbittorrent_port: int | None = None
    qbittorrent_username: str | None = None
    qbittorrent_password: SecretStr | None = None
    notify_enabled: bool | None = None
    notify_webhook_url: SecretStr | None = None
    notify_telegram_bot_token: SecretStr | None = None
    notify_telegram_chat_id: str | None = None
    notify_events: list[str] | None = None
    upgrade_threshold: float | None = None
    upgrade_max_per_episode: int | None = None
    upgrade_copy_policy: str | None = None
    upgrade_skip_size_gb: float | None = None
    mismatch_backfill_budget: int | None = None
    naming_title_language: str | None = None
    naming_movie_dir: bool | None = None
    naming_specials_s00: bool | None = None
    naming_year_suffix: bool | None = None
    rss_fetch_timeout_s: float | None = None
    rss_fetch_retries: int | None = None


class SettingsUpdateOut(SettingsOut):
    """PUT 响应：常规载荷 + 每个被改字段的生效档位（与调度重建警告）。"""

    applied: dict[str, SettingEffect]
    warnings: list[str] = []


class ChannelTestOut(BaseModel):
    """notify-test 逐通道结果（error 只含异常类型名，不含 URL/token）。"""

    channel: str
    ok: bool
    error: str | None = None


class NotifyTestOut(BaseModel):
    results: list[ChannelTestOut]


class QbitTestOut(BaseModel):
    """qbit-test 结果（version 为 qBittorrent 服务端版本号）。

    save_path = qB 全局默认保存路径（/app/preferences），供前端与
    download_path 比对提示对齐；拿不到为 None，不作为 ok 的条件。
    """

    ok: bool
    version: str | None = None
    save_path: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Metrics（/api/metrics）
# ---------------------------------------------------------------------------


class LevelStatsOut(BaseModel):
    level: int
    total: int
    llm_called: int
    outcomes: dict[str, int]


class CurvePointOut(BaseModel):
    bucket: str
    total: int
    llm_called: int
    llm_rate: float | None


class PendingTrendPointOut(BaseModel):
    bucket: str
    created: int
    resolved: int


class MemorySourceStatsOut(BaseModel):
    source: str
    status: str
    rows: int


class MetricsOut(BaseModel):
    """Dashboard 汇总（ARCHITECTURE §5.5 / §5.0b 口径）。"""

    intervention_rate: float | None
    audit_total: int
    audit_manual: int
    by_level: list[LevelStatsOut]
    llm_call_curve_weekly: list[CurvePointOut]
    pending_trend_daily: list[PendingTrendPointOut]
    pending_open: int
    episode_states: dict[str, int]
    memory_sources: list[MemorySourceStatsOut]


# ---------------------------------------------------------------------------
# Pipeline confirm-name / Episodes reparse（12-F）
# ---------------------------------------------------------------------------


class ConfirmNameIn(BaseModel):
    """库外人工确认学习（12-F）：name 必填，其余字段缺省回退 L1 草稿。

    字段风格与 ``PendingConfirmIn`` 一致；行为等价 CLI ``confirm``
    （同一 store 入口：学习三件套 + pending 收尾 + hardlink 归档）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=1000)
    title: str | None = None
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    segment: str | None = None
    fansub: str | None = None


class ConfirmNameOut(BaseModel):
    """确认结果（与 CLI confirm 的 JSON 输出同字段口径）。"""

    bypassed: bool
    resolved_pending: int
    archive: dict[str, object]
    entries: list[dict[str, object]]


class EpisodeReparseIn(BaseModel):
    """集重新识别（12-F 两步契约）：dry_run=true 预览、false 实际执行。"""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True


class EpisodeReparseOut(BaseModel):
    episode_id: int
    dry_run: bool
    parsed: dict[str, object] | None
    action: dict[str, object]
