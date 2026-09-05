from __future__ import annotations

from enum import StrEnum


class EpisodeState(StrEnum):
    MISSING = "missing"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    ORGANIZED = "organized"
    UPGRADED = "upgraded"
    IGNORED = "ignored"
    # B5（E4 增量）：启动对账发现 ORGANIZED 文件已不在盘上 → 标 FLAGGED +
    # 通知，不自动修；文件恢复/手动处理后可回到 ORGANIZED。
    FLAGGED = "flagged"

    def can_transition(self, target: EpisodeState) -> bool:
        transitions = {
            self.MISSING: frozenset({self.DOWNLOADING, self.IGNORED}),
            self.DOWNLOADING: frozenset({self.DOWNLOADED, self.IGNORED}),
            self.DOWNLOADED: frozenset({self.ORGANIZED, self.IGNORED}),
            self.ORGANIZED: frozenset({self.UPGRADED, self.FLAGGED}),
            self.UPGRADED: frozenset({self.ORGANIZED}),
            self.IGNORED: frozenset(),
            self.FLAGGED: frozenset({self.ORGANIZED}),
        }
        return target in transitions[self]


class ReleaseStatus(StrEnum):
    """release_record 下载任务生命周期（审核 B4，E4 增量）。

    ``candidate``（RSS 新条目入池）→ ``picked``（已选定提交下载器）→
    ``downloading``（网关确认在下载）→ ``completed``（完成，待归档/已归档
    归档侧另看 episode 状态）/ ``failed``（失败，可重试回 ``picked``，
    重试次数上界由网关轮询侧约束 ≤2，不进库）。
    """

    CANDIDATE = "candidate"
    PICKED = "picked"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"

    def can_transition(self, target: ReleaseStatus) -> bool:
        transitions = {
            self.CANDIDATE: frozenset({self.PICKED, self.FAILED}),
            self.PICKED: frozenset({self.DOWNLOADING, self.FAILED, self.CANDIDATE}),
            self.DOWNLOADING: frozenset({self.COMPLETED, self.FAILED}),
            self.COMPLETED: frozenset(),
            self.FAILED: frozenset({self.PICKED}),
        }
        return target in transitions[self]


class SeasonState(StrEnum):
    UPCOMING = "upcoming"
    AIRING = "airing"
    ENDED = "ended"
    COLLECTED = "collected"

    def can_transition(self, target: SeasonState) -> bool:
        transitions = {
            self.UPCOMING: frozenset({self.AIRING}),
            self.AIRING: frozenset({self.ENDED}),
            self.ENDED: frozenset({self.COLLECTED}),
            self.COLLECTED: frozenset(),
        }
        return target in transitions[self]


class MediaType(StrEnum):
    TV = "tv"
    MOVIE = "movie"
    OVA = "ova"
    SPECIAL = "special"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Segment(StrEnum):
    EPISODE = "episode"
    SEASON_PACK = "season_pack"
    MOVIE = "movie"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING = "pending"


class MemorySource(StrEnum):
    MANUAL = "manual"
    LLM_CONFIRMED = "llm_confirmed"
    LLM_AUTO = "llm_auto"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    DEPRECATED = "deprecated"


class PendingStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    SKIPPED = "skipped"


class ResolvedBy(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class Actor(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
