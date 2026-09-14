"""memory.alias 的 title_aliases 读写 + ExpectedContext 别名并入单测（P0-B 别名富化）。

覆盖：``upsert_title_aliases``（shape 归一、幂等去重、self 映射跳过、
source=bangumi 标记）、``alias_titles_for``（canonical 形状反查、占位符
形状剔除、与给定标题自身形状去重）、``ExpectedContext.titles()`` 并入
extra_titles（保序、去重、空值跳过）。
"""

from __future__ import annotations

from typing import Any

import pytest

from autoanime.core.models import TitleAlias
from autoanime.memory.alias import AliasService
from autoanime.memory.store import SqliteStorage
from autoanime.organize.expected import ExpectedContext

_OPEN_STORAGES: list[SqliteStorage] = []


@pytest.fixture(autouse=True)
async def _close_storages() -> Any:
    yield
    for storage in _OPEN_STORAGES:
        await storage.close()
    _OPEN_STORAGES.clear()


@pytest.fixture
async def storage() -> SqliteStorage:
    row = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await row.create_all()
    _OPEN_STORAGES.append(row)
    return row


async def test_upsert_title_aliases_normalizes_and_dedupes(storage: SqliteStorage) -> None:
    service = AliasService(storage)
    written = await service.upsert_title_aliases(
        "尼古喵喵", ["Yani Neko", "yani  neko", "Chainsmoker Cat"], source="bangumi"
    )
    # "Yani Neko" 与 "yani  neko" 归一同 shape → 去重后 2 条
    assert written == 2
    rows = await storage.list(TitleAlias)
    assert {row.source for row in rows} == {"bangumi"}
    assert all(row.canonical_shape == "尼古喵喵" for row in rows)


async def test_upsert_title_aliases_idempotent_and_skips_self_maps(storage: SqliteStorage) -> None:
    service = AliasService(storage)
    await service.upsert_title_aliases("尼古喵喵", ["Yani Neko"], source="bangumi")
    # 幂等：重复写入不产生新行（主键 title_shape_norm 兜底）
    written = await service.upsert_title_aliases("尼古喵喵", ["Yani Neko"], source="bangumi")
    assert written == 1
    rows = await storage.list(TitleAlias)
    assert len(rows) == 1
    # self 映射（别名 == canonical）被 put_alias_map 跳过
    written = await service.upsert_title_aliases("尼古喵喵", ["尼古喵喵"], source="bangumi")
    assert written == 0
    assert len(await storage.list(TitleAlias)) == 1


async def test_alias_titles_for_reverse_lookup(storage: SqliteStorage) -> None:
    service = AliasService(storage)
    await service.upsert_title_aliases(
        "尼古喵喵",
        ["Yani Neko", "Chainsmoker Cat", "尼古喵喵 第二季", "Neko Season 2"],
        source="bangumi",
    )
    # canonical 形状反查命中；self（订阅标题自身形状）与占位符形状被剔除
    # （"Neko Season 2" 归一后含 {season} 占位符，不能当标题文本喂 title_matches）
    titles = await service.alias_titles_for(("尼古喵喵", "ヤニねこ", None))
    assert titles == ("yani neko", "chainsmoker cat", "尼古喵喵 第二季")
    # 查询不命中 → 空
    assert await service.alias_titles_for(("孤独摇滚",)) == ()
    assert await service.alias_titles_for(()) == ()


async def test_expected_context_titles_merges_extra_titles() -> None:
    context = ExpectedContext(
        series_id=1,
        season_number=1,
        episode_number=0,
        title_cn="尼古喵喵",
        title_jp=None,
        title_romaji="Yani Neko",
        extra_titles=("yani neko", "chainsmoker cat"),
    )
    # 三标题在前、别名在后；与三标题重复的别名不重复；None 标题跳过
    assert context.titles() == ("尼古喵喵", "Yani Neko", "yani neko", "chainsmoker cat")
    # 无别名 = 现状语义（回归保护）
    plain = ExpectedContext(series_id=1, season_number=1, episode_number=1, title_cn="孤独摇滚")
    assert plain.titles() == ("孤独摇滚",)
