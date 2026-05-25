"""
Schema v2 路径、空结构体与原子写 JSON
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..config_loader import Auxiliary_GetCacheStorePath


def Auxiliary_GetV2DataDir() -> Path:
    return Auxiliary_GetCacheStorePath().parent


def Auxiliary_GetV2SubfilePath(subfile: str) -> Path:
    m = {
        "organization": "organization.json",
        "titles": "titles.json",
        "api": "api_responses.json",
        "api_responses": "api_responses.json",
    }
    return Auxiliary_GetV2DataDir() / m.get(subfile, f"{subfile}.json")


def Auxiliary_Sha256File(P: Path) -> str:
    if P.is_file() is False:
        return ""
    h = hashlib.sha256()
    with open(P, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def Auxiliary_AtomicWriteJson(P: Path, Data: Any) -> None:
    P.parent.mkdir(parents=True, exist_ok=True)
    Tmp = P.with_suffix(P.suffix + ".tmp")
    with open(Tmp, "w", encoding="utf-8") as f:
        json.dump(Data, f, ensure_ascii=False, indent=2)
    Tmp.replace(P)


V2_VERSION = 2

EMPTY_ORGANIZATION: dict = {
    "__meta__": {"schema_version": V2_VERSION, "updated_at": None},
    "records": {},
}

EMPTY_TITLES: dict = {
    "__meta__": {"schema_version": V2_VERSION, "updated_at": None},
    "canonicals": {},
    "aliases": {},
}

EMPTY_API_RESPONSES: dict = {
    "__meta__": {"schema_version": V2_VERSION},
    "tmdb": {"titles": {}, "titles_en": {}, "tv_series": {}, "tv_seasons": {}},
    "bangumi": {"titles": {}},
    "openai_identify": {"file_info": {}},
    "ext": {},
}


def Auxiliary_WriteV2CacheMeta(
    subfile_stats: Optional[dict] = None,
    legacy_archive: Optional[str] = None,
) -> None:
    """仅更新 meta 文件（flush 时由 persistent 调）。subfile_stats 为可选的部分覆盖。"""
    p = Auxiliary_GetV2DataDir() / "cache_meta.json"
    existing: dict = {}
    if p.is_file():
        try:
            with open(p, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    now = datetime.now().replace(microsecond=0).isoformat()
    if type(existing) is not dict or existing.get("schema_version") != V2_VERSION:
        existing = {
            "schema_version": V2_VERSION,
            "created_at": now,
            "subfiles": {},
        }
    existing["last_flush_at"] = now
    if legacy_archive is not None:
        existing["legacy_archive"] = legacy_archive
    if type(subfile_stats) is dict:
        sf = existing.get("subfiles", {})
        if type(sf) is not dict:
            sf = {}
        for k, v in subfile_stats.items():
            sf[k] = v
        existing["subfiles"] = sf
    Auxiliary_AtomicWriteJson(p, existing)
