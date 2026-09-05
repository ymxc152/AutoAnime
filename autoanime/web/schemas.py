"""Web 层 pydantic 请求/响应 schema（E2 M3 后端）。

只做参数校验与序列化组装；不承载业务规则。所有列表端点统一
``limit/offset`` 分页（``Page[T]`` 信封：total/limit/offset/items）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """统一分页信封：total 为过滤条件下的总行数。"""

    total: int
    limit: int
    offset: int
    items: list[T]


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
    season: int | None = None
    episode: int | None = None
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


class OperationGroupOut(BaseModel):
    """按 operation_id 分组的 audit 汇总（Logs 页时间线展开用）。"""

    operation_id: str
    rows: int
    entities: list[str]
    actions: list[str]
    first_audit_id: int
    last_audit_id: int


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
    seasons: list[SeasonProgressOut]


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
    status: str | None = None
    fansub_pref: str | None = None
    quality_pref: str | None = None


# ---------------------------------------------------------------------------
# RSS sources（/api/rss_sources，B3）
# ---------------------------------------------------------------------------


class RssSourceOut(BaseModel):
    id: int
    url: str
    # token 永不回显（SecretStr 也不序列化明文，读取端点直接不返回）。
    has_token: bool
    season_id: int
    enabled: bool
    last_polled_at: datetime | None


class RssSourceCreateIn(BaseModel):
    url: str
    token: SecretStr | None = None
    season_id: int
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("url must be a non-empty string")
        return value


class RssSourceUpdateIn(BaseModel):
    url: str | None = None
    token: SecretStr | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Settings（/api/settings）
# ---------------------------------------------------------------------------


class SettingsOut(BaseModel):
    """运行时可见项（密钥一律不回显，只给 has_* 布尔）。"""

    dry_run: bool
    l2_enabled: bool
    llm_enabled: bool
    llm_model: str | None
    reference_enabled: bool
    reference_order: list[str]
    library_path: str
    download_path: str
    api_host: str
    api_port: int
    api_cors_dev_origins: list[str]
    api_sse_heartbeat_s: float
    api_sse_replay_limit: int
    has_api_token: bool
    has_llm_api_key: bool


class SettingsUpdateIn(BaseModel):
    """可运行时覆写的项（PUT 作用于进程内 Settings 实例）。"""

    dry_run: bool | None = None
    l2_enabled: bool | None = None
    llm_enabled: bool | None = None
    llm_model: str | None = None
    reference_enabled: bool | None = None
    reference_order: list[str] | None = None


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
