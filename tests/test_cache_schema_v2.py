# -*- coding: utf-8 -*-
"""Schema v2 缓存：子文件路由、信任校验、原子写、迁移、兼容层、cache_doctor。"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoanime import state
from autoanime.cache import persistent
from autoanime.cache.canonical import Auxiliary_LinkAliasToCanonical, Auxiliary_UpsertCanonicalTitle
from autoanime.cache.migrate import Auxiliary_MigrateCacheToV2IfNeeded
from autoanime.cache.trust import Auxiliary_ValidateAliasWrite
from autoanime.cache.v2_data import Auxiliary_AtomicWriteJson, Auxiliary_GetV2DataDir
from autoanime.config_loader import Auxiliary_InitRuntimeContext


def _init_cache_in_tmp(base: Path) -> None:
    state.init_defaults()
    state.PRINTLOGFLAG = False
    cdir = base / ".cache"
    cdir.mkdir(parents=True, exist_ok=True)
    state.CACHE_DIR = str(cdir)
    Auxiliary_InitRuntimeContext()
    state.PersistentApiCache = {}
    state.PersistentApiCacheDirty = False
    state.CacheSubfileDirty = {
        "organization": False,
        "titles": False,
        "api_responses": False,
    }
    state.TitleAliasIndexDataCache = {}
    state.CanonicalTitleIndexDataCache = {}
    state.ShowOrganizationIndexDataCache = {}


def _load_save():
    persistent.Auxiliary_LoadPersistentCache()
    return persistent.Auxiliary_SavePersistentCache


def _read_json(p: Path) -> dict:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


class TestCacheV2Route(unittest.TestCase):
    """1. Set/Get 路由到正确子文件"""

    def test_groups_map_to_subfiles(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _init_cache_in_tmp(base)
            Auxiliary_MigrateCacheToV2IfNeeded()
            _load_save()
            persistent.Auxiliary_SetPersistentCache("TMDB", "q1", "标题A")
            persistent.Auxiliary_SetPersistentCache("ShowOrganizationIndex", "c1", {"canonical_id": "c1", "title_zh": "番"})
            persistent.Auxiliary_SavePersistentCache(force=True)
            ap = Auxiliary_GetV2DataDir() / "api_responses.json"
            op = Auxiliary_GetV2DataDir() / "organization.json"
            self.assertTrue(ap.is_file())
            self.assertTrue(op.is_file())
            aj = _read_json(ap)
            oj = _read_json(op)
            tmdb_titles = (aj.get("tmdb") or {}).get("titles", {})
            self.assertIn("q1", tmdb_titles)
            self.assertEqual(tmdb_titles["q1"].get("value"), "标题A")
            self.assertIn("c1", oj.get("records", {}))
            self.assertEqual(persistent.Auxiliary_GetPersistentCache("TMDB", "q1"), "标题A")
            r = persistent.Auxiliary_GetPersistentCache("ShowOrganizationIndex", "c1")
            self.assertIsInstance(r, dict)
            self.assertEqual(r.get("title_zh"), "番")


class TestCacheV2Trust(unittest.TestCase):
    """2–4. 拒绝原因与信任：长键 / 低 trust / locked"""

    def test_alias_key_too_long_rejected(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _init_cache_in_tmp(base)
            _load_save()
            cid, _ = Auxiliary_UpsertCanonicalTitle("测试剧名", "En", "", "TMDB", [])
            self.assertIsNotNone(cid)
            long_key = "x" * 101
            ok, reason = Auxiliary_ValidateAliasWrite(long_key, str(cid), 80, new_source="TMDB")
            self.assertFalse(ok)
            self.assertEqual(reason, "alias_key_too_long")

    def test_lower_trust_does_not_override(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _init_cache_in_tmp(base)
            _load_save()
            cid, _ = Auxiliary_UpsertCanonicalTitle("低信任测", "L", "", "TMDB", [])
            self.assertIsNotNone(cid)
            cid2, _ = Auxiliary_UpsertCanonicalTitle("另一部", "O", "", "TMDB", [])
            self.assertIsNotNone(cid2)
            Auxiliary_LinkAliasToCanonical("onealias", cid, "TMDB")
            Auxiliary_LinkAliasToCanonical("onealias", cid2, "openai_identify")
            v = persistent.Auxiliary_GetPersistentCache("TitleAliasIndex", "onealias")
            self.assertEqual(str(v), str(cid))

    def test_locked_canonical_rejects_auto_alias(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _init_cache_in_tmp(base)
            _load_save()
            cid, _ = Auxiliary_UpsertCanonicalTitle("锁定测", "E", "", "TMDB", [])
            rec = {
                "zh": "锁定测",
                "en": "E",
                "romaji": "",
                "source": "TMDB",
                "confidence": 80,
                "locked": True,
            }
            persistent.Auxiliary_SetPersistentCache("CanonicalTitleIndex", cid, rec)
            state.CanonicalTitleIndexDataCache[cid] = rec
            persistent.Auxiliary_SavePersistentCache(force=True)
            Auxiliary_LinkAliasToCanonical("新别名键", cid, "TMDB")
            self.assertIsNone(persistent.Auxiliary_GetPersistentCache("TitleAliasIndex", "新别名键"))


class TestCacheV2Atomic(unittest.TestCase):
    """5. 原子写：未 replace 前主文件内容保持"""

    def test_atomic_write_old_unchanged_if_tmp_incomplete(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "sample.json"
            p.write_text('{"a": 1}\n', encoding="utf-8")
            t = p.with_suffix(p.suffix + ".tmp")
            t.write_text("partial", encoding="utf-8")
            self.assertEqual(_read_json(p), {"a": 1})
            t.unlink()
            Auxiliary_AtomicWriteJson(p, {"b": 2})
            self.assertEqual(_read_json(p), {"b": 2})


class TestCacheV2IncrementalFlush(unittest.TestCase):
    """6. 只刷 organization 时 titles 文件不被覆盖（mtime）"""

    def test_only_organization_subfile_touched(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            _init_cache_in_tmp(base)
            Auxiliary_MigrateCacheToV2IfNeeded()
            _load_save()
            persistent.Auxiliary_SetPersistentCache("TMDB", "init", "v")
            persistent.Auxiliary_SavePersistentCache(force=True)
            titles = Auxiliary_GetV2DataDir() / "titles.json"
            t0 = os.path.getmtime(titles)
            persistent.Auxiliary_SetPersistentCache(
                "ShowOrganizationIndex",
                "o1",
                {"canonical_id": "o1", "title_zh": "仅进度"},
            )
            persistent.Auxiliary_SavePersistentCache(force=False)
            t1 = os.path.getmtime(titles)
            self.assertEqual(t0, t1)


class TestCacheV2Migrate(unittest.TestCase):
    """7. 迁移：旧 monolithic 归档 + v2 空表"""

    def test_legacy_api_cache_archived_and_v2_init(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cdir = base / ".cache"
            cdir.mkdir(parents=True, exist_ok=True)
            legacy = cdir / "api_cache.json"
            legacy.write_text("{}", encoding="utf-8")
            state.init_defaults()
            state.CACHE_DIR = str(cdir)
            state.PRINTLOGFLAG = False
            Auxiliary_InitRuntimeContext()
            Auxiliary_MigrateCacheToV2IfNeeded()
            self.assertTrue((cdir / "cache_meta.json").is_file())
            self.assertFalse(legacy.is_file())
            backs = list((cdir / "backups").glob("api_cache_legacy_*.json"))
            self.assertEqual(len(backs), 1)
            oj = _read_json(cdir / "organization.json")
            self.assertEqual(oj.get("records"), {})


class TestCacheV2ApiCompatible(unittest.TestCase):
    """8. 对外 API 签名不变：Get/Set 行为"""

    def test_get_set_public_surface(self):
        self.assertTrue(callable(persistent.Auxiliary_GetPersistentCache))
        self.assertTrue(callable(persistent.Auxiliary_SetPersistentCache))
        self.assertTrue(callable(persistent.Auxiliary_MaybeFlushPersistentCache))
        with TemporaryDirectory() as tmp:
            _init_cache_in_tmp(Path(tmp))
            Auxiliary_MigrateCacheToV2IfNeeded()
            _load_save()
            persistent.Auxiliary_SetPersistentCache("Bangumi", "k", {"name": 1})
            v = persistent.Auxiliary_GetPersistentCache("Bangumi", "k")
            self.assertEqual(v, {"name": 1})


class TestCacheDoctorInspect(unittest.TestCase):
    """9. cache_doctor --inspect 能跑通并返回码"""

    def test_inspect_subprocess(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cdir = base / ".cache"
            cdir.mkdir(parents=True, exist_ok=True)
            from autoanime.cache.v2_data import (
                EMPTY_API_RESPONSES,
                EMPTY_ORGANIZATION,
                EMPTY_TITLES,
            )

            Auxiliary_AtomicWriteJson(cdir / "organization.json", dict(EMPTY_ORGANIZATION))
            Auxiliary_AtomicWriteJson(cdir / "titles.json", dict(EMPTY_TITLES))
            Auxiliary_AtomicWriteJson(cdir / "api_responses.json", dict(EMPTY_API_RESPONSES))
            Auxiliary_AtomicWriteJson(
                cdir / "cache_meta.json",
                {"schema_version": 2, "subfiles": {}, "created_at": "x"},
            )
            root = Path(__file__).resolve().parent.parent
            script = root / "scripts" / "cache_doctor.py"
            r = subprocess.run(
                [sys.executable, str(script), "--inspect", "--cache-dir", str(cdir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            self.assertIn("schema_version=2", r.stdout)
            self.assertIn("organization.json", r.stdout)


class TestCacheV2Composite(unittest.TestCase):
    """10. 综合：revert 与 doctor export（依赖 audit 行）"""

    def test_revert_and_export_audit(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cdir = base / ".cache"
            cdir.mkdir(parents=True, exist_ok=True)
            from autoanime.cache.v2_data import (
                EMPTY_TITLES,
            )

            Auxiliary_AtomicWriteJson(
                cdir / "cache_meta.json",
                {"schema_version": 2, "subfiles": {}},
            )
            tdata = dict(EMPTY_TITLES)
            tdata["aliases"]["akey"] = {
                "canonical_id": "c1",
                "trust_level": 80,
                "source": "T",
                "added_at": "t",
            }
            Auxiliary_AtomicWriteJson(cdir / "titles.json", tdata)
            ev = {
                "audit_id": "test-audit-001",
                "ts": 1e9,
                "type": "alias_written",
                "alias_key": "akey",
                "canonical_id": "c1",
            }
            with open(cdir / "pollution_audit.jsonl", "w", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            root = Path(__file__).resolve().parent.parent
            script = root / "scripts" / "cache_doctor.py"
            r0 = subprocess.run(
                [sys.executable, str(script), "--export-audit", "--since", "2000-01-01", "--cache-dir", str(cdir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r0.returncode, 0, msg=r0.stderr)
            r1 = subprocess.run(
                [sys.executable, str(script), "--revert", "--audit-id", "test-audit-001", "--cache-dir", str(cdir)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r1.returncode, 0, msg=r1.stderr)
            t2 = _read_json(cdir / "titles.json")
            self.assertNotIn("akey", t2.get("aliases", {}))


if __name__ == "__main__":
    unittest.main()
