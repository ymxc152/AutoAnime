"""Read-only metadata adapter whose failure never blocks file organization."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MetadataResult:
    available: bool
    status: str
    poster_url: Optional[str] = None
    synopsis: Optional[str] = None
    broadcast_status: Optional[str] = None
    error: Optional[str] = None


class SafeMetadataAdapter:
    def __init__(self, provider):
        self.provider = provider

    def fetch(self, title):
        try:
            value = self.provider(title)
            if isinstance(value, MetadataResult):
                return value
            return MetadataResult(
                True,
                "available",
                value.get("poster_url"),
                value.get("synopsis"),
                value.get("broadcast_status"),
            )
        except Exception as error:
            return MetadataResult(False, "unavailable", error=str(error))
