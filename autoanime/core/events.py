from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class EventCategory(StrEnum):
    PARSE = "parse"
    DOWNLOAD = "download"
    ORGANIZE = "organize"
    ERROR = "error"
    NOTIFY = "notify"
    SYSTEM = "system"


@dataclass(frozen=True)
class Event:
    category: EventCategory
    message: str
    payload: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class EventBus(Protocol):
    """Bus placeholder; PR1 intentionally does not provide a concrete implementation."""

    async def publish(self, event: Event) -> None: ...
