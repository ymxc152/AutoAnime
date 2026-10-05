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

from collections.abc import Iterable

from autoanime.core.models import Alias, TitleAlias
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2.keys import level1_key

DEFAULT_ALIAS_SOURCE = "manual"
BANGUMI_ALIAS_SOURCE = "bangumi"
"""订阅创建路径（P0-B 别名富化）写 title_aliases 时的 source 标记。"""


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
        rows = await self._store.find_aliases_by_norm(alias_norm(title))
        return [row.series_id for row in rows]

    async def find_series_id(self, title: str) -> int | None:
        """First series id matching the title, or ``None``."""
        ids = await self.find_series_ids(title)
        return ids[0] if ids else None

    async def aliases_for_series(self, series_id: int) -> list[Alias]:
        """Every alias row registered for a series, ordered by id."""
        return await self._store.find_aliases_by_series(series_id)

    # --- title_aliases 窄表（PR7 M3）读/写：订阅别名富化（P0-B）用 --------

    async def upsert_title_aliases(
        self,
        canonical_title: str,
        alias_titles: Iterable[str],
        *,
        source: str = BANGUMI_ALIAS_SOURCE,
    ) -> int:
        """把一批别名以「alias shape → canonical shape」upsert 进 title_aliases。

        ``canonical_title`` 是该 series 的权威名（订阅标题——Bangumi 侧
        name_cn/name 通常即中文名）；每个别名著 ``build_title_shape`` 归一
        后经 ``SqliteStorage.put_alias_map`` 幂等写入（主键
        ``title_shape_norm`` 去重，self 映射 alias==canonical 跳过）。
        返回实际写入的映射条数（去重/self 跳过不计）。
        """
        canonical_shape = alias_norm(canonical_title)
        if not canonical_shape:
            return 0
        mapping: dict[str, str] = {}
        for alias_title in alias_titles:
            if not isinstance(alias_title, str):
                continue
            shape = alias_norm(alias_title)
            # self 映射（别名归一后 == canonical）不写（canonical 自身不是别名）。
            if shape and shape != canonical_shape:
                mapping[shape] = canonical_shape
        if not mapping:
            return 0
        await self._store.put_alias_map(mapping, source)
        return len(mapping)

    async def alias_titles_for(self, titles: Iterable[str | None]) -> tuple[str, ...]:
        """查回可并入 expected_titles 的别名标题（按 canonical 形状反查）。

        给定一个 series 的标题（订阅三标题），取 ``title_aliases`` 中
        ``canonical_shape`` 命中其中任一形状的行，返回其别名形状文本。
        只回无占位符的形状（``{season}``/``{ep}`` 模板不能当标题文本喂给
        ``title_matches``）；与给定标题自身形状相同的行剔除（不是别名）。
        """
        wanted = {alias_norm(t) for t in titles if isinstance(t, str) and t.strip()}
        wanted.discard("")
        if not wanted:
            return ()
        # 单用户本地库 title_aliases 体量小（confirm/订阅富化逐条累积），
        # 泛型 list + 内存过滤即可，不必为反查加专用 SQL 读侧。
        rows: list[TitleAlias] = await self._store.list(TitleAlias)
        result: list[str] = []
        for row in rows:
            shape = row.title_shape_norm
            if row.canonical_shape not in wanted or shape in wanted:
                continue
            if "{" in shape or "}" in shape or shape in result:
                continue
            result.append(shape)
        return tuple(result)

    async def _find(self, series_id: int, normalized: str) -> Alias | None:
        rows = await self._store.find_aliases_by_norm(normalized)
        return next((row for row in rows if row.series_id == series_id), None)
