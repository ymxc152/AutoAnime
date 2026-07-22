from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class MediaFile:
    path: Path
    input_root: Path
    context_name: str
    relative_path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ParsedName:
    raw_title: str
    season: Optional[int]
    episode: Optional[int]
    is_movie: bool = False
    explicit_season: bool = False
    explicit_episode: bool = False
    title_candidates: Tuple[str, ...] = ()
    release_tag: str = ""
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    agent: str
    value: str
    confidence: float
    detail: str = ""


@dataclass
class Resolution:
    media: MediaFile
    canonical_title: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None
    is_movie: bool = False
    confidence: float = 0.0
    accepted: bool = False
    release_tag: str = ""
    evidence: List[Evidence] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fingerprint: str = ""

    def identity_key(self) -> Tuple[str, int, int, bool]:
        return (
            self.canonical_title,
            int(self.season or 0),
            int(self.episode or 0),
            bool(self.is_movie),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": str(self.media.path),
            "relative_path": self.media.relative_path,
            "title": self.canonical_title,
            "season": self.season,
            "episode": self.episode,
            "is_movie": self.is_movie,
            "confidence": round(self.confidence, 4),
            "accepted": self.accepted,
            "release_tag": self.release_tag,
            "warnings": list(self.warnings),
            "evidence": [e.__dict__ for e in self.evidence],
            "fingerprint": self.fingerprint,
        }


@dataclass
class PlanEntry:
    source: Path
    destination: Optional[Path]
    action: str
    resolution: Resolution
    reason: str = ""
    companion_of: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": str(self.source),
            "destination": str(self.destination) if self.destination else "",
            "action": self.action,
            "reason": self.reason,
            "companion_of": self.companion_of,
            "resolution": self.resolution.to_dict(),
        }
