from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from autoanime.core.enums import (
    Actor,
    Decision,
    EpisodeState,
    MediaType,
    MemorySource,
    MemoryStatus,
    PendingStatus,
    ResolvedBy,
    SeasonState,
)


class Base(DeclarativeBase):
    pass


def _enum(enum_cls: type[Any]) -> Any:
    return Enum(enum_cls, native_enum=False, values_callable=lambda x: [e.value for e in x])


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (
        CheckConstraint(
            "title_cn IS NOT NULL OR title_jp IS NOT NULL OR title_romaji IS NOT NULL",
            name="ck_series_title",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_cn: Mapped[str | None] = mapped_column(String, nullable=True)
    title_jp: Mapped[str | None] = mapped_column(String, nullable=True)
    title_romaji: Mapped[str | None] = mapped_column(String, nullable=True)
    media_type: Mapped[MediaType] = mapped_column(_enum(MediaType), default=MediaType.TV)
    tmdb_id: Mapped[str | None] = mapped_column(String, nullable=True)
    bangumi_id: Mapped[str | None] = mapped_column(String, nullable=True)
    fansub_pref: Mapped[str | None] = mapped_column(String, nullable=True)
    quality_pref: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")


class Season(Base):
    __tablename__ = "season"
    __table_args__ = (UniqueConstraint("series_id", "number", name="uq_season_series_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), nullable=False)
    number: Mapped[int]
    status: Mapped[SeasonState] = mapped_column(_enum(SeasonState), default=SeasonState.UPCOMING)


class Episode(Base):
    __tablename__ = "episode"

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), nullable=False)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season.id"), nullable=True)
    number: Mapped[int]
    state: Mapped[EpisodeState] = mapped_column(_enum(EpisodeState), default=EpisodeState.MISSING)
    upgraded_count: Mapped[int] = mapped_column(default=0)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    air_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class ReleaseRecord(Base):
    __tablename__ = "release_record"
    __table_args__ = (
        CheckConstraint(
            "(season_id IS NOT NULL AND episode_id IS NULL) OR "
            "(season_id IS NULL AND episode_id IS NOT NULL)",
            name="ck_release_record_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season.id"), nullable=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episode.id"), nullable=True)
    torrent_hash: Mapped[str] = mapped_column(String, unique=True)
    fansub: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int | None] = mapped_column(nullable=True)
    seeders: Mapped[int | None] = mapped_column(nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[Decision] = mapped_column(_enum(Decision), default=Decision.PENDING)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)


class ParseMemory(Base):
    __tablename__ = "parse_memory"
    __table_args__ = (
        CheckConstraint("key_level IN (1, 2)", name="ck_parse_memory_key_level"),
        UniqueConstraint("key_level", "key_hash", name="uq_parse_memory_key"),
        Index("ix_parse_memory_key_level_hash", "key_level", "key_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key_level: Mapped[int]
    key_hash: Mapped[str]
    fansub_norm: Mapped[str | None] = mapped_column(String, nullable=True)
    title_shape: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source: Mapped[MemorySource] = mapped_column(_enum(MemorySource), default=MemorySource.MANUAL)
    hit_count: Mapped[int] = mapped_column(default=0)
    corrected_count: Mapped[int] = mapped_column(default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[MemoryStatus] = mapped_column(_enum(MemoryStatus), default=MemoryStatus.ACTIVE)


class Alias(Base):
    __tablename__ = "alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), nullable=False)
    alias_norm: Mapped[str]
    source: Mapped[str]


class BypassList(Base):
    __tablename__ = "bypass_list"
    __table_args__ = (Index("ix_bypass_list_pattern_hash", "pattern_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_hash: Mapped[str]
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PendingQueue(Base):
    __tablename__ = "pending_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_name: Mapped[str]
    context: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    stage: Mapped[str]
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[PendingStatus] = mapped_column(_enum(PendingStatus), default=PendingStatus.PENDING)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_by: Mapped[ResolvedBy | None] = mapped_column(_enum(ResolvedBy), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str]
    entity: Mapped[str]
    entity_id: Mapped[int | None] = mapped_column(nullable=True)
    action: Mapped[str]
    instruction: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    reverse: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    actor: Mapped[Actor] = mapped_column(_enum(Actor), default=Actor.AUTO)


class ParseEvents(Base):
    __tablename__ = "parse_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column(Date)
    raw_name_hash: Mapped[str]
    level: Mapped[int]
    llm_called: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    outcome: Mapped[str]
    confidence: Mapped[str | None] = mapped_column(String, nullable=True)


class LlmCacheRow(Base):
    """``llm_cache`` 表：L3 录制的真实 LLM 响应（PR5）。

    命名为 ``LlmCacheRow`` 以区别于 ``pipeline.l3.cache_key.LlmCache``
    （store 层 Protocol 数据类）；键与 bypass 同源（``pattern_hash``），
    每 pattern 至多一行。``response_text`` 存录制的模型输出原文，回放时
    走与真实调用相同的严格 schema 解析。``request_fingerprint`` 为可选
    审计列，当前写入路径（Protocol ``put``）不填充。
    """

    __tablename__ = "llm_cache"
    __table_args__ = (UniqueConstraint("pattern_hash", name="uq_llm_cache_pattern_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_hash: Mapped[str]
    request_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    response_text: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ReferenceCache(Base):
    """``reference_cache`` 表：参考源剧目级缓存（PR6 P2）。

    以 ``(title_shape, provider)`` 为键，每对至多一行：``title_shape``
    是 L2 规范化标题形状（casefold、占位符化），``provider`` 是参考源
    注册名（如 ``"bangumi"``）。``facts`` 存 ``ReferenceFacts`` 形状的
    JSON（负缓存存 ``{"negative": true}`` 标记）；``expires_at`` 为空
    表示永不过期，非空由读取方与当前时间比较判定失效。
    """

    __tablename__ = "reference_cache"
    __table_args__ = (
        UniqueConstraint("title_shape", "provider", name="uq_reference_cache_shape_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title_shape: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    facts: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
