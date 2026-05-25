"""
Schema v2 缓存子文件：路径布局、dataclass、原子 JSON 读写、cache_meta 子文件 sha256 统计。

供 migrate / persistent 改造 / audit 等模块复用；不直接替代现有 api_cache.json 读写逻辑，
直至上层接入完成。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..config_loader import Auxiliary_GetCacheStorePath


SCHEMA_VERSION_V2 = 2

FILENAME_CACHE_META = 'cache_meta.json'
FILENAME_ORGANIZATION = 'organization.json'
FILENAME_TITLES = 'titles.json'
FILENAME_API_RESPONSES = 'api_responses.json'
FILENAME_POLLUTION_AUDIT = 'pollution_audit.jsonl'
DIR_BACKUPS = 'backups'


def Auxiliary_SchemaV2NowIso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S%z')


@dataclass(frozen=True)
class SchemaV2Layout:
    """`.cache/` 下 Schema v2 各文件路径（与 `api_cache.json` 同目录）。"""

    cache_dir: Path

    @property
    def cache_meta(self) -> Path:
        return self.cache_dir / FILENAME_CACHE_META

    @property
    def organization(self) -> Path:
        return self.cache_dir / FILENAME_ORGANIZATION

    @property
    def titles(self) -> Path:
        return self.cache_dir / FILENAME_TITLES

    @property
    def api_responses(self) -> Path:
        return self.cache_dir / FILENAME_API_RESPONSES

    @property
    def pollution_audit(self) -> Path:
        return self.cache_dir / FILENAME_POLLUTION_AUDIT

    @property
    def backups_dir(self) -> Path:
        return self.cache_dir / DIR_BACKUPS


def Auxiliary_SchemaV2LayoutFromRuntime() -> SchemaV2Layout:
    return SchemaV2Layout(cache_dir=Auxiliary_GetCacheStorePath().parent)


@dataclass
class SchemaV2SubfileDescriptor:
    """单个子文件在 cache_meta.subfiles 中的统计描述（写入 meta 前填充）。"""

    sha256: str = ''
    updated_at: str = ''
    records: int = 0
    canonicals: int = 0
    aliases: int = 0
    entries: int = 0

    def to_meta_dict(self) -> Dict[str, Any]:
        Out: Dict[str, Any] = {'sha256': self.sha256, 'updated_at': self.updated_at}
        if self.records > 0:
            Out['records'] = self.records
        if self.canonicals > 0:
            Out['canonicals'] = self.canonicals
        if self.aliases > 0:
            Out['aliases'] = self.aliases
        if self.entries > 0:
            Out['entries'] = self.entries
        return Out


@dataclass
class SchemaV2CacheMetaDocument:
    """cache_meta.json 内存表示（与 JSON 字段对齐）。"""

    schema_version: int = SCHEMA_VERSION_V2
    created_at: str = ''
    last_flush_at: str = ''
    subfiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    legacy_archive: Optional[str] = None

    def to_json_dict(self) -> Dict[str, Any]:
        D: Dict[str, Any] = {
            'schema_version': self.schema_version,
            'created_at': self.created_at,
            'last_flush_at': self.last_flush_at,
            'subfiles': dict(self.subfiles),
        }
        if self.legacy_archive not in [None, '']:
            D['legacy_archive'] = self.legacy_archive
        return D

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> SchemaV2CacheMetaDocument:
        return cls(
            schema_version=int(data.get('schema_version', SCHEMA_VERSION_V2)),
            created_at=str(data.get('created_at', '')),
            last_flush_at=str(data.get('last_flush_at', '')),
            subfiles=dict(data.get('subfiles', {})) if type(data.get('subfiles')) == dict else {},
            legacy_archive=data.get('legacy_archive'),
        )


def Auxiliary_SchemaV2ComputeSha256(path: Path) -> str:
    if path.is_file() != True:
        return ''
    H = hashlib.sha256()
    with open(path, 'rb') as F:
        for Chunk in iter(lambda: F.read(1024 * 1024), b''):
            H.update(Chunk)
    return H.hexdigest()


def Auxiliary_SchemaV2AtomicReadJson(path: Path) -> Optional[Dict[str, Any]]:
    if path.is_file() != True:
        return None
    try:
        with open(path, 'r', encoding='UTF-8') as F:
            Data = json.load(F)
        if type(Data) == dict:
            return Data
    except Exception:
        return None
    return None


def Auxiliary_SchemaV2AtomicWriteJson(path: Path, data: Any, *, indent: int = 2, sort_keys: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    TmpPath = path.with_suffix(path.suffix + '.tmp')
    with open(TmpPath, 'w', encoding='UTF-8') as F:
        json.dump(data, F, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
        F.flush()
        os.fsync(F.fileno())
    os.replace(TmpPath, path)


def Auxiliary_SchemaV2CountOrganizationRecords(doc: Dict[str, Any]) -> int:
    Rec = doc.get('records', {})
    if type(Rec) == dict:
        return len(Rec)
    return 0


def Auxiliary_SchemaV2CountTitles(doc: Dict[str, Any]) -> tuple:
    C = doc.get('canonicals', {})
    A = doc.get('aliases', {})
    Cn = len(C) if type(C) == dict else 0
    An = len(A) if type(A) == dict else 0
    return Cn, An


def Auxiliary_SchemaV2CountApiEntries(doc: Dict[str, Any]) -> int:
    def CountTtlLeaves(Obj: Any) -> int:
        if type(Obj) != dict:
            return 0
        if 'value' in Obj and 'ts' in Obj:
            return 1
        return sum(CountTtlLeaves(V) for V in Obj.values())

    Total = 0
    for TopKey in ('tmdb', 'bangumi', 'openai_identify'):
        Sub = doc.get(TopKey, {})
        if type(Sub) == dict:
            Total += CountTtlLeaves(Sub)
    return Total


def Auxiliary_SchemaV2BuildSubfileDescriptor(path: Path, role: str) -> SchemaV2SubfileDescriptor:
    Updated = ''
    if path.is_file():
        try:
            Updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S%z')
        except Exception:
            Updated = Auxiliary_SchemaV2NowIso()
    Doc = Auxiliary_SchemaV2AtomicReadJson(path)
    Sha = Auxiliary_SchemaV2ComputeSha256(path)
    Desc = SchemaV2SubfileDescriptor(sha256=Sha, updated_at=Updated)
    if Doc is None:
        return Desc
    if role == 'organization':
        Desc.records = Auxiliary_SchemaV2CountOrganizationRecords(Doc)
    elif role == 'titles':
        Cn, An = Auxiliary_SchemaV2CountTitles(Doc)
        Desc.canonicals = Cn
        Desc.aliases = An
    elif role == 'api_responses':
        Desc.entries = Auxiliary_SchemaV2CountApiEntries(Doc)
    return Desc


def Auxiliary_SchemaV2RefreshSubfilesInMeta(
    meta_doc: SchemaV2CacheMetaDocument,
    layout: SchemaV2Layout,
) -> SchemaV2CacheMetaDocument:
    """根据磁盘上子文件重算 sha256 与各计数，写回 meta_doc.subfiles。"""
    Sub: Dict[str, Dict[str, Any]] = {}
    Org = Auxiliary_SchemaV2BuildSubfileDescriptor(layout.organization, 'organization')
    Sub[FILENAME_ORGANIZATION] = Org.to_meta_dict()
    Tit = Auxiliary_SchemaV2BuildSubfileDescriptor(layout.titles, 'titles')
    Sub[FILENAME_TITLES] = Tit.to_meta_dict()
    Api = Auxiliary_SchemaV2BuildSubfileDescriptor(layout.api_responses, 'api_responses')
    Sub[FILENAME_API_RESPONSES] = Api.to_meta_dict()
    meta_doc.subfiles = Sub
    return meta_doc


def Auxiliary_SchemaV2WriteCacheMeta(layout: SchemaV2Layout, meta_doc: SchemaV2CacheMetaDocument) -> None:
    Auxiliary_SchemaV2AtomicWriteJson(layout.cache_meta, meta_doc.to_json_dict())
