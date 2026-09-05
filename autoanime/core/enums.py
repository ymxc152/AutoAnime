from __future__ import annotations

from enum import StrEnum


class EpisodeState(StrEnum):
    MISSING = "missing"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    ORGANIZED = "organized"
    UPGRADED = "upgraded"
    IGNORED = "ignored"

    def can_transition(self, target: EpisodeState) -> bool:
        transitions = {
            self.MISSING: frozenset({self.DOWNLOADING, self.IGNORED}),
            self.DOWNLOADING: frozenset({self.DOWNLOADED, self.IGNORED}),
            self.DOWNLOADED: frozenset({self.ORGANIZED, self.IGNORED}),
            self.ORGANIZED: frozenset({self.UPGRADED}),
            self.UPGRADED: frozenset({self.ORGANIZED}),
            self.IGNORED: frozenset(),
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
