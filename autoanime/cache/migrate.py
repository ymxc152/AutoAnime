"""
一次性格式迁移：将旧 `.cache/api_cache.json` 归档为 `backups/api_cache_legacy_<RunID>.json`，
并初始化空 Schema v2 子文件与 `cache_meta.json`（零数据冷启动）。
"""

from typing import Optional

from .. import state
from ..logging_utils import Auxiliary_Log
from .v2_data import (
    EMPTY_API_RESPONSES,
    EMPTY_ORGANIZATION,
    EMPTY_TITLES,
    Auxiliary_AtomicWriteJson,
    Auxiliary_GetV2DataDir,
    Auxiliary_GetV2SubfilePath,
    Auxiliary_Sha256File,
    Auxiliary_WriteV2CacheMeta,
)


def Auxiliary_MigrateCacheToV2IfNeeded() -> Optional[str]:
    """
    若已有 `cache_meta.json` 则直接返回 None。
    否则：若存在旧 `api_cache.json` 则移入 `backups/`，并写入空 v2 子文件 + meta。
    返回 `legacy_archive` 全路径或 None（无归档）。
    """
    base = Auxiliary_GetV2DataDir()
    base.mkdir(parents=True, exist_ok=True)
    meta = base / "cache_meta.json"
    if meta.is_file():
        return None
    legacy = base / "api_cache.json"
    archive: Optional[str] = None
    if legacy.is_file():
        bdir = base / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        dest = bdir / f"api_cache_legacy_{state.CurrentRunID}.json"
        try:
            legacy.replace(dest)
            archive = str(dest)
            Auxiliary_Log(
                f"旧 api_cache.json 已归档至 {dest}，新 Schema v2 启用（零数据冷启动）", "INFO"
            )
        except Exception as err:
            Auxiliary_Log(f"归档旧 api_cache.json 失败: {err}，将尝试在原地保留", "WARNING")
    for name, data in [
        ("organization", EMPTY_ORGANIZATION),
        ("titles", EMPTY_TITLES),
        ("api", EMPTY_API_RESPONSES),
    ]:
        Auxiliary_AtomicWriteJson(Auxiliary_GetV2SubfilePath(name), data)
    org = Auxiliary_GetV2SubfilePath("organization")
    titles = Auxiliary_GetV2SubfilePath("titles")
    api = Auxiliary_GetV2SubfilePath("api")
    from datetime import datetime

    now = datetime.now().replace(microsecond=0).isoformat()
    Auxiliary_WriteV2CacheMeta(
        subfile_stats={
            "organization.json": {
                "sha256": Auxiliary_Sha256File(org),
                "records": 0,
                "updated_at": now,
            },
            "titles.json": {
                "sha256": Auxiliary_Sha256File(titles),
                "canonicals": 0,
                "aliases": 0,
                "updated_at": now,
            },
            "api_responses.json": {
                "sha256": Auxiliary_Sha256File(api),
                "entries": 0,
                "updated_at": now,
            },
        },
        legacy_archive=archive,
    )
    return archive
