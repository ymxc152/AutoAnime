"""
autoanime 持久化缓存读写（Schema v2：多子文件 + 路由 + 增量脏刷）

对外保持：
- `Auxiliary_LoadPersistentCache` / `Auxiliary_SavePersistentCache`
- `Auxiliary_GetPersistentCache` / `Auxiliary_SetPersistentCache`
- `Auxiliary_MaybeFlushPersistentCache`
- `Auxiliary_RebuildCanonicalIndexesFromPersistentCache`
"""

import json
from copy import deepcopy
from datetime import datetime
from time import time
from typing import Any, Dict, Tuple

from .. import state
from ..config_loader import Auxiliary_GetCacheStorePath, Auxiliary_ParseInt
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


def Auxiliary_GetCacheDir():
    return Auxiliary_GetV2DataDir()


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# 子文件 key -> organization | titles | api
def _mark_subfile_dirty(key: str) -> None:
    if hasattr(state, "CacheSubfileDirty") and type(state.CacheSubfileDirty) is dict:
        state.CacheSubfileDirty[key] = True
    state.PersistentApiCacheDirty = True


def _default_ttl_for_group(CacheGroup: str) -> int:
    g = str(CacheGroup)
    if g in ("TMDB", "TMDB_EN", "Bangumi", "BGM"):
        return 86400
    if g in ("TMDBTvSeriesId", "TMDBTvSeasons"):
        return 604800
    return int(state.Runtime.config.cache_ttl_seconds) if state.Runtime and state.Runtime.config else 86400


_NEVER_EXPIRE = {"TitleAliasIndex", "CanonicalTitleIndex", "ShowOrganizationIndex"}

# CacheGroup -> (api 根下路径, 叶子 dict 名); 非 API 在 _subfile_for 处理
_API_PATH: Dict[str, Tuple[str, ...]] = {
    "TMDB": ("tmdb", "titles"),
    "TMDB_EN": ("tmdb", "titles_en"),
    "TMDBTvSeriesId": ("tmdb", "tv_series"),
    "TMDBTvSeasons": ("tmdb", "tv_seasons"),
    "Bangumi": ("bangumi", "titles"),
    "BGM": ("ext", "BGM"),
}


def _subfile_key_for_group(CacheGroup: str) -> str:
    g = str(CacheGroup)
    if g == "ShowOrganizationIndex":
        return "organization"
    if g in ("CanonicalTitleIndex", "TitleAliasIndex"):
        return "titles"
    return "api_responses"


def _is_v2_layout() -> bool:
    return (Auxiliary_GetV2DataDir() / "cache_meta.json").is_file()


def _deep_get(obj: Any, parts: Tuple[str, ...]) -> Any:
    cur = obj
    for p in parts:
        if type(cur) is not dict or p not in cur:
            return None
        cur = cur[p]
    return cur


def _deep_ensure(obj: Any, parts: Tuple[str, ...]) -> dict:
    cur = obj
    for p in parts:
        if p not in cur or type(cur[p]) is not dict:
            cur[p] = {}
        cur = cur[p]
    return cur


def _load_legacy_monolithic(CacheFilePath) -> None:
    if CacheFilePath.is_file() is False:
        return
    try:
        with open(CacheFilePath, "r", encoding="UTF-8") as CacheFile:
            CacheData = json.load(CacheFile)
        if type(CacheData) == dict:
            state.PersistentApiCache = CacheData
            Auxiliary_Log(f"已加载持久化缓存文件 {CacheFilePath}", "INFO")
    except Exception as err:
        Auxiliary_Log(f"缓存文件读取失败，将使用空缓存: {err}", "WARNING")
        state.PersistentApiCache = {}


def _load_v2_into_memory() -> None:
    orgp = Auxiliary_GetV2SubfilePath("organization")
    tpath = Auxiliary_GetV2SubfilePath("titles")
    apath = Auxiliary_GetV2SubfilePath("api")
    odata: dict = {}
    tdata: dict = {}
    adata: dict = {}
    try:
        if orgp.is_file():
            with open(orgp, "r", encoding="utf-8") as f:
                odata = json.load(f)
    except Exception:
        odata = {}
    try:
        if tpath.is_file():
            with open(tpath, "r", encoding="utf-8") as f:
                tdata = json.load(f)
    except Exception:
        tdata = {}
    try:
        if apath.is_file():
            with open(apath, "r", encoding="utf-8") as f:
                adata = json.load(f)
    except Exception:
        adata = {}
    if type(odata) is not dict:
        odata = {}
    if type(tdata) is not dict:
        tdata = {}
    if type(adata) is not dict:
        adata = {}
    state.PersistentApiCache = {}
    recs = odata.get("records", {})
    if type(recs) is dict:
        state.PersistentApiCache["ShowOrganizationIndex"] = {}
        for cid, rec in recs.items():
            if rec in [None, ""]:
                continue
            state.PersistentApiCache["ShowOrganizationIndex"][str(cid)] = {"value": rec, "ts": 0.0}
    cans = tdata.get("canonicals", {})
    if type(cans) is dict:
        state.PersistentApiCache["CanonicalTitleIndex"] = {}
        for cid, crec in cans.items():
            state.PersistentApiCache["CanonicalTitleIndex"][str(cid)] = {"value": crec, "ts": 0.0}
    als = tdata.get("aliases", {})
    if type(als) is dict:
        state.PersistentApiCache["TitleAliasIndex"] = {}
        for akey, arec in als.items():
            if type(arec) is str:
                state.PersistentApiCache["TitleAliasIndex"][str(akey)] = {
                    "value": arec,
                    "ts": 0.0,
                    "trust_level": 50,
                    "source": "",
                    "added_at": "",
                }
            elif type(arec) is dict:
                cid = arec.get("canonical_id", arec.get("value", ""))
                state.PersistentApiCache["TitleAliasIndex"][str(akey)] = {
                    "value": cid,
                    "ts": 0.0,
                    "trust_level": int(arec.get("trust_level", 80) or 80),
                    "source": str(arec.get("source", "")),
                    "added_at": str(arec.get("added_at", "")),
                }
    for grp, path_parts in _API_PATH.items():
        bucket = _deep_get(adata, path_parts)
        if type(bucket) is not dict:
            continue
        if grp not in state.PersistentApiCache:
            state.PersistentApiCache[grp] = {}
        for ck, crec in bucket.items():
            if type(crec) is not dict:
                continue
            state.PersistentApiCache[grp][str(ck)] = {
                "value": crec.get("value"),
                "ts": float(crec.get("ts", 0) or 0),
                "ttl": int(crec.get("ttl", _default_ttl_for_group(grp) or 86400) or 86400),
            }
    # ext 下非 _API_PATH 表内键名的其它组
    ext = adata.get("ext", {})
    if type(ext) is dict:
        for gname, bucket in ext.items():
            gn = str(gname)
            if gn in _API_PATH:
                continue
            if type(bucket) is not dict:
                continue
            if gn not in state.PersistentApiCache:
                state.PersistentApiCache[gn] = {}
            for ck, crec in bucket.items():
                if type(crec) is not dict:
                    continue
                state.PersistentApiCache[gn][str(ck)] = {
                    "value": crec.get("value"),
                    "ts": float(crec.get("ts", 0) or 0),
                    "ttl": int(crec.get("ttl", _default_ttl_for_group(gn) or 86400) or 86400),
                }
    Auxiliary_Log(
        f"已加载 Schema v2 缓存 {Auxiliary_GetV2DataDir()} (organization / titles / api_responses)", "INFO"
    )


def _dump_organization_json() -> dict:
    root = deepcopy(EMPTY_ORGANIZATION)
    meta = root["__meta__"]
    if type(meta) is dict:
        meta["updated_at"] = _now_iso()
    grp = state.PersistentApiCache.get("ShowOrganizationIndex", {})
    if type(grp) is not dict:
        return root
    out = root["records"]
    for cid, ent in grp.items():
        if type(ent) is dict and "value" in ent:
            out[str(cid)] = ent["value"]
    return root


def _dump_titles_json() -> dict:
    root = deepcopy(EMPTY_TITLES)
    meta = root["__meta__"]
    if type(meta) is dict:
        meta["updated_at"] = _now_iso()
    cg = state.PersistentApiCache.get("CanonicalTitleIndex", {})
    if type(cg) is dict:
        for cid, ent in cg.items():
            if type(ent) is dict and "value" in ent and type(ent["value"]) is dict:
                root["canonicals"][str(cid)] = ent["value"]
    ag = state.PersistentApiCache.get("TitleAliasIndex", {})
    if type(ag) is dict:
        for ak, ent in ag.items():
            if type(ent) is not dict or "value" not in ent:
                continue
            v = ent["value"]
            root["aliases"][str(ak)] = {
                "canonical_id": v,
                "trust_level": int(ent.get("trust_level", 50) or 50),
                "source": str(ent.get("source", "")),
                "added_at": str(ent.get("added_at", "")),
            }
    return root


def _dump_api_json() -> dict:
    base = deepcopy(EMPTY_API_RESPONSES)
    for grp, path_parts in _API_PATH.items():
        mem = state.PersistentApiCache.get(grp, {})
        if type(mem) is not dict:
            continue
        bucket = _deep_ensure(base, path_parts)
        for ck, ent in mem.items():
            if type(ent) is not dict:
                continue
            bucket[str(ck)] = {
                "value": ent.get("value"),
                "ts": float(ent.get("ts", 0) or 0),
                "ttl": int(
                    ent.get("ttl", _default_ttl_for_group(grp) or 86400) or 86400
                ),
            }
    known_api = set(_API_PATH.keys())
    for gname, mem in (state.PersistentApiCache or {}).items():
        if gname in _NEVER_EXPIRE or gname in known_api:
            continue
        if type(mem) is not dict:
            continue
        ex = _deep_ensure(base, ("ext", str(gname)))
        for ck, ent in mem.items():
            if type(ent) is not dict:
                continue
            ex[str(ck)] = {
                "value": ent.get("value"),
                "ts": float(ent.get("ts", 0) or 0),
                "ttl": int(
                    ent.get("ttl", _default_ttl_for_group(str(gname)) or 86400) or 86400
                ),
            }
    return base


def _flush_one_subfile(name: str) -> None:
    if name == "organization":
        data = _dump_organization_json()
        p = Auxiliary_GetV2SubfilePath("organization")
        Auxiliary_AtomicWriteJson(p, data)
        n = len((data.get("records") or {})) if type(data) is dict else 0
        Auxiliary_WriteV2CacheMeta(
            {
                "organization.json": {
                    "sha256": Auxiliary_Sha256File(p),
                    "records": n,
                    "updated_at": _now_iso(),
                }
            }
        )
    elif name == "titles":
        data = _dump_titles_json()
        p = Auxiliary_GetV2SubfilePath("titles")
        Auxiliary_AtomicWriteJson(p, data)
        nc = len((data.get("canonicals") or {})) if type(data) is dict else 0
        na = len((data.get("aliases") or {})) if type(data) is dict else 0
        Auxiliary_WriteV2CacheMeta(
            {
                "titles.json": {
                    "sha256": Auxiliary_Sha256File(p),
                    "canonicals": nc,
                    "aliases": na,
                    "updated_at": _now_iso(),
                }
            }
        )
    elif name == "api_responses":
        data = _dump_api_json()
        p = Auxiliary_GetV2SubfilePath("api")
        Auxiliary_AtomicWriteJson(p, data)
        ent_n = 0
        if type(data) is dict:
            tmdb = data.get("tmdb", {})
            b = data.get("bangumi", {})
            ext = data.get("ext", {})
            for sec in (tmdb, b):
                if type(sec) is dict:
                    for _k, bkt in sec.items():
                        if type(bkt) is dict:
                            ent_n += len(bkt)
            oa = (data.get("openai_identify") or {}).get("file_info", {})
            if type(oa) is dict:
                ent_n += len(oa)
            if type(ext) is dict:
                for _gn, bkt in ext.items():
                    if type(bkt) is dict:
                        ent_n += len(bkt)
        Auxiliary_WriteV2CacheMeta(
            {
                "api_responses.json": {
                    "sha256": Auxiliary_Sha256File(p),
                    "entries": ent_n,
                    "updated_at": _now_iso(),
                }
            }
        )


def Auxiliary_LoadPersistentCache():
    from .migrate import Auxiliary_MigrateCacheToV2IfNeeded

    state.PersistentApiCache = {}
    state.PersistentApiCacheDirty = False
    if hasattr(state, "CacheSubfileDirty") and type(state.CacheSubfileDirty) is dict:
        for k in list(state.CacheSubfileDirty.keys()):
            state.CacheSubfileDirty[k] = False
    else:
        state.CacheSubfileDirty = {
            "organization": False,
            "titles": False,
            "api_responses": False,
        }
    Auxiliary_MigrateCacheToV2IfNeeded()
    if _is_v2_layout():
        _load_v2_into_memory()
    else:
        _load_legacy_monolithic(Auxiliary_GetCacheStorePath())
    Auxiliary_RebuildCanonicalIndexesFromPersistentCache()


def _needs_persistent_save(force: bool) -> bool:
    if force is True:
        return True
    if state.PersistentApiCacheDirty is True:
        return True
    if _is_v2_layout() and type(getattr(state, "CacheSubfileDirty", None)) is dict:
        return any(state.CacheSubfileDirty.values())
    return False


def Auxiliary_SavePersistentCache(force=False):
    if _needs_persistent_save(force) is not True:
        return
    if not _is_v2_layout():
        P = Auxiliary_GetCacheStorePath()
        try:
            with open(P, "w", encoding="utf-8") as f:
                json.dump(state.PersistentApiCache, f, ensure_ascii=False, indent=2, sort_keys=True)
            state.PersistentApiCacheDirty = False
            Auxiliary_Log(f"持久化缓存写入完成 {P}", "INFO")
        except Exception as err:
            Auxiliary_Log(f"持久化缓存写入失败: {err}", "WARNING")
        return
    dirty = state.CacheSubfileDirty if type(getattr(state, "CacheSubfileDirty", None)) is dict else {}
    order = ("organization", "titles", "api_responses")
    for k in order:
        if force is True or dirty.get(k) is True:
            try:
                _flush_one_subfile(k)
            except Exception as err:
                Auxiliary_Log(f"v2 子文件写入失败 {k}: {err}", "WARNING")
    for k in list((state.CacheSubfileDirty or {}).keys()):
        state.CacheSubfileDirty[k] = False
    state.PersistentApiCacheDirty = False
    Auxiliary_Log(
        f"Schema v2 持久化缓存已写入 {Auxiliary_GetV2DataDir()}", "INFO"
    )


def Auxiliary_GetPersistentCache(CacheGroup, CacheKey):
    if CacheGroup not in state.PersistentApiCache:
        return None
    GroupCache = state.PersistentApiCache[CacheGroup]
    if type(GroupCache) is not dict or CacheKey not in GroupCache:
        return None
    CacheRecord = GroupCache[CacheKey]
    if type(CacheRecord) is not dict:
        return None
    CacheValue = CacheRecord.get("value")
    CacheTimestamp = float(CacheRecord.get("ts", 0) or 0)
    g = str(CacheGroup)
    if g in _NEVER_EXPIRE:
        TTLValue = 0
    else:
        TTLValue = int(CacheRecord.get("ttl", 0) or 0) or _default_ttl_for_group(g)
    if (
        g not in _NEVER_EXPIRE
        and TTLValue > 0
        and (time() - CacheTimestamp) > float(TTLValue)
    ):
        try:
            del GroupCache[CacheKey]
            _mark_subfile_dirty(_subfile_key_for_group(g))
        except Exception:
            pass
        return None
    return CacheValue


def Auxiliary_SetPersistentCache(CacheGroup, CacheKey, CacheValue):
    g = str(CacheGroup)
    if g not in state.PersistentApiCache or type(state.PersistentApiCache[g]) is not dict:
        state.PersistentApiCache[g] = {}
    rec = {"value": CacheValue, "ts": time()}
    if g in _NEVER_EXPIRE:
        pass
    else:
        rec["ttl"] = _default_ttl_for_group(g)
    state.PersistentApiCache[g][CacheKey] = rec
    _mark_subfile_dirty(_subfile_key_for_group(g))


def Auxiliary_MaybeFlushPersistentCache():
    Interval = Auxiliary_ParseInt(state.CACHE_FLUSH_INTERVAL_SECONDS, 60)
    if Interval <= 0:
        return
    if state.PersistentApiCacheDirty is not True:
        return
    NowTs = time()
    if NowTs - float(state.LastPersistentCacheFlushTime or 0.0) < float(Interval):
        return
    Auxiliary_SavePersistentCache(force=True)
    state.LastPersistentCacheFlushTime = NowTs


def Auxiliary_SetPersistentCacheAliasWithMeta(
    CacheKey, canonical_id, *, trust_level, source, added_at: str
) -> None:
    """
    仅 `canonical.LinkAlias` 使用：在 TitleAliasIndex 中写入带 trust 的包装记录。
    """
    g = "TitleAliasIndex"
    if g not in state.PersistentApiCache or type(state.PersistentApiCache[g]) is not dict:
        state.PersistentApiCache[g] = {}
    state.PersistentApiCache[g][str(CacheKey)] = {
        "value": str(canonical_id),
        "ts": 0.0,
        "trust_level": int(trust_level),
        "source": str(source or ""),
        "added_at": str(added_at or ""),
    }
    _mark_subfile_dirty("titles")


def Auxiliary_DelPersistentCacheAlias(AliasKey):
    """从 TitleAliasIndex 删除一条别名（同步内存态与持久层）。"""
    g = "TitleAliasIndex"
    if type(state.PersistentApiCache) is dict:
        gc = state.PersistentApiCache.get(g)
        if type(gc) is dict and str(AliasKey) in gc:
            del gc[str(AliasKey)]
            _mark_subfile_dirty("titles")
    if hasattr(state, "TitleAliasIndexDataCache") and type(state.TitleAliasIndexDataCache) is dict:
        state.TitleAliasIndexDataCache.pop(str(AliasKey), None)


def Auxiliary_RebuildCanonicalIndexesFromPersistentCache():
    from .canonical import Auxiliary_UpsertCanonicalTitle
    from ..text_utils import (
        Auxiliary_HasChineseText,
        Auxiliary_NormalizeApiTitle,
        Auxiliary_NormalizeDisplayTitle,
    )

    if type(state.PersistentApiCache) is not dict:
        return

    def IterateRawGroupValue(CacheGroup):
        GroupData = state.PersistentApiCache.get(CacheGroup, {})
        if type(GroupData) is not dict:
            return []
        ReturnList = []
        for CacheKey, CacheRecord in GroupData.items():
            if type(CacheRecord) is dict and "value" in CacheRecord:
                ReturnList.append((CacheKey, CacheRecord.get("value")))
        return ReturnList

    ChangedFlag = False
    for CacheGroup in ["Bangumi", "TMDB"]:
        for QueryName, CacheValue in IterateRawGroupValue(CacheGroup):
            if CacheValue in [None, ""]:
                continue
            CandidateZh = Auxiliary_NormalizeApiTitle(CacheValue)
            CandidateEn = Auxiliary_NormalizeDisplayTitle(
                QueryName if QueryName not in [None, ""] else ""
            )
            if Auxiliary_HasChineseText(CandidateZh) is False:
                if CandidateEn in [None, ""]:
                    CandidateEn = Auxiliary_NormalizeDisplayTitle(CacheValue)
                CandidateZh = ""
            CanonicalID, CanonicalZh = Auxiliary_UpsertCanonicalTitle(
                CandidateZh, CandidateEn, "", CacheGroup, [QueryName, CacheValue],
            )
            if CanonicalID not in [None, ""]:
                if CanonicalZh not in [None, ""] and Auxiliary_HasChineseText(CanonicalZh):
                    if type(state.PersistentApiCache.get(CacheGroup, {}).get(QueryName)) is dict:
                        if state.PersistentApiCache[CacheGroup][QueryName].get("value") != CanonicalZh:
                            state.PersistentApiCache[CacheGroup][QueryName]["value"] = CanonicalZh
                            ChangedFlag = True
                            _mark_subfile_dirty("api_responses")
    for QueryName, CacheValue in IterateRawGroupValue("TMDB_EN"):
        if CacheValue in [None, ""]:
            continue
        EnTitle = Auxiliary_NormalizeDisplayTitle(str(CacheValue))
        if EnTitle in [None, ""]:
            continue
        Auxiliary_UpsertCanonicalTitle("", EnTitle, "", "TMDB", [QueryName, EnTitle])
    for CanonicalKey, CacheValue in IterateRawGroupValue("ShowOrganizationIndex"):
        if type(CacheValue) is not dict:
            continue
        zh = Auxiliary_NormalizeApiTitle(CacheValue.get("title_zh", ""))
        en = Auxiliary_NormalizeDisplayTitle(CacheValue.get("title_en", ""))
        romaji = Auxiliary_NormalizeDisplayTitle(CacheValue.get("title_romaji", ""))
        if (
            zh not in [None, ""]
            or en not in [None, ""]
            or romaji not in [None, ""]
        ):
            Auxiliary_UpsertCanonicalTitle(zh, en, romaji, "unknown", [CanonicalKey])
    if ChangedFlag is True:
        state.PersistentApiCacheDirty = True
