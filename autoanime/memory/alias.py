"""Alias helpers for series-level cross-fansub / cross-title memory hits.

The T1 series-level key (``level1_key``) already ignores the fansub, so the
alias table's job is the remaining gap: different *titles* for the same
series (CN / JP / romaji variants) must resolve to one series. An alias is
stored under ``alias_norm`` -- exactly the T1 title-shape normalization that
``level1_key`` is built on -- so lookup is equality of normalized shapes.

Pure normalization here; every DB session stays inside :class:`AliasService`
on top of the generic ``SqliteStorage`` API.
"""

from __future__ import annotations

from autoanime.core.models import Alias
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2.keys import level1_key

DEFAULT_ALIAS_SOURCE = "manual"


def alias_norm(title: str) -> str:
    """Series-level alias normalization: the T1 title-shape normalization.

    Deliberately identical to ``level1_key``: an alias and a parsed title
    match when their title shapes are equal (casefolded, separators folded,
    season/episode markers abstracted to placeholders).
    """
    return level1_key(title)


class AliasService:
    """Alias writes and lookups on top of SqliteStorage."""

    def __init__(self, store: SqliteStorage) -> None:
        self._store = store

    async def add_alias(
        self, series_id: int, alias_title: str, *, source: str = DEFAULT_ALIAS_SOURCE
    ) -> Alias:
        """Register one alias title for a series; idempotent on alias_norm."""
        normalized = alias_norm(alias_title)
        existing = await self._find(series_id, normalized)
        if existing is not None:
            return existing
        row = Alias(series_id=series_id, alias_norm=normalized, source=source)
        await self._store.add(row)
        return row

    async def find_series_ids(self, title: str) -> list[int]:
        """Series ids whose registered aliases normalize to the title's shape.

        More than one series may legitimately claim an ambiguous title; the
        result is ordered by alias row id (registration order).
        """
        normalized = alias_norm(title)
        matches = [
            row
            for row in await self._store.list(Alias)
            if row.alias_norm == normalized
        ]
        matches.sort(key=lambda row: row.id)
        return [row.series_id for row in matches]

    async def find_series_id(self, title: str) -> int | None:
        """First series id matching the title, or ``None``."""
        ids = await self.find_series_ids(title)
        return ids[0] if ids else None

    async def aliases_for_series(self, series_id: int) -> list[Alias]:
        """Every alias row registered for a series, ordered by id."""
        rows = [row for row in await self._store.list(Alias) if row.series_id == series_id]
        rows.sort(key=lambda row: row.id)
        return rows

    async def _find(self, series_id: int, normalized: str) -> Alias | None:
        for row in await self._store.list(Alias):
            if row.series_id == series_id and row.alias_norm == normalized:
                return row
        return None
