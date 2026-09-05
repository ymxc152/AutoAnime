from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from autoanime.core.enums import Confidence, Segment
from autoanime.core.events import Event

T = TypeVar("T")


@dataclass(frozen=True)
class RawName:
    name: str
    folder: str | None = None
    parent_path: str | None = None


@dataclass(frozen=True)
class ParseContext:
    known_series: int | None = None
    release_progress: int | None = None
    fansub_pref: str | None = None


@dataclass(frozen=True)
class ParseResult:
    title: str
    season: int | None
    episode: int | None
    segment: Segment
    fansub: str | None
    level: Confidence
    confidence: float
    missing_fields: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TorrentSource:
    url: str
    hash: str | None = None
    name: str | None = None


@runtime_checkable
class Recognizer(Protocol):
    async def parse(self, raw: RawName, context: ParseContext | None = None) -> ParseResult | None: ...


@runtime_checkable
class MetadataProvider(Protocol):
    async def search(self, query: str) -> list[dict[str, object]]: ...
    async def get_detail(self, provider_id: int) -> dict[str, object] | None: ...
    async def get_season_episodes(self, provider_id: int, season: int) -> list[dict[str, object]]: ...


@runtime_checkable
class Downloader(Protocol):
    async def add(self, source: TorrentSource) -> str: ...
    async def status(self, torrent_hash: str) -> dict[str, object] | None: ...


@runtime_checkable
class Notifier(Protocol):
    async def send(self, event: Event) -> None: ...


@runtime_checkable
class Storage(Protocol):
    async def create_all(self) -> None: ...
    async def add(self, obj: Any) -> None: ...
    async def get(self, model: type[Any], id_: int) -> Any | None: ...
    async def list(self, model: type[Any]) -> list[Any]: ...
    async def delete(self, obj: Any) -> None: ...
    async def close(self) -> None: ...


class Registry:
    """Small explicit registry used only for external providers and gateways."""

    def __init__(self) -> None:
        self._services: dict[type[object], dict[str, object]] = {}

    def register(self, protocol: type[object], name: str) -> Callable[[T], T]:
        def decorator(value: T) -> T:
            bucket = self._services.setdefault(protocol, {})
            bucket[name] = value
            return value

        return decorator

    def get(self, protocol: type[object], name: str | None = None) -> object:
        bucket = self._services.get(protocol)
        if bucket is None:
            raise KeyError(protocol)
        if name is None:
            if len(bucket) != 1:
                raise KeyError(f"protocol has multiple implementations: {protocol}")
            return next(iter(bucket.values()))
        if name not in bucket:
            raise KeyError(name)
        return bucket[name]

    def optional(self, protocol: type[object], name: str | None = None) -> object | None:
        try:
            return self.get(protocol, name)
        except KeyError:
            return None
