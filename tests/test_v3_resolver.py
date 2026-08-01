import os
import tempfile
import unittest
from pathlib import Path

from autoanime_v3.cache import ResolutionCache, fingerprint
from autoanime_v3.catalog import TitleCatalog
from autoanime_v3.config import AppConfig
from autoanime_v3.models import MediaFile
from autoanime_v3.resolver import Resolver


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def media(self, name, context="下载"):
        path = self.root / name
        path.write_bytes(b"video")
        stat = path.stat()
        return MediaFile(path, self.root, context, name, stat.st_size, stat.st_mtime_ns)

    def config(self):
        return AppConfig(self.root / "library.sqlite3", self.root / "aliases.json")

    def test_catalog_resolution_is_accepted(self):
        catalog = TitleCatalog({"sousounofrieren": "葬送的芙莉莲"}, {"葬送的芙莉莲": [28, 28]})
        with ResolutionCache(self.config().cache_path) as cache:
            result = Resolver(catalog, self.config(), cache).resolve(self.media("Sousou no Frieren - 38.mkv"))
        self.assertTrue(result.accepted)
        self.assertEqual((result.canonical_title, result.season, result.episode), ("葬送的芙莉莲", 2, 10))

    def test_unknown_english_title_requires_review(self):
        with ResolutionCache(self.config().cache_path) as cache:
            result = Resolver(TitleCatalog({}, {}), self.config(), cache).resolve(self.media("Unknown Anime S01E03.mkv"))
        self.assertFalse(result.accepted)
        self.assertIn("unverified_non_chinese_title", result.warnings)

    def test_chinese_title_is_accepted_without_remote_api(self):
        with ResolutionCache(self.config().cache_path) as cache:
            result = Resolver(TitleCatalog({}, {}), self.config(), cache).resolve(self.media("[ANi] 摩緒 - 03 [1080P].mp4"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.canonical_title, "摩绪")

    def test_catalog_can_define_single_file_special_default(self):
        catalog = TitleCatalog({"somepv": "某动画 PV"}, {}, {"somepv": (0, 1)})
        with ResolutionCache(self.config().cache_path) as cache:
            result = Resolver(catalog, self.config(), cache).resolve(self.media("Some PV.mkv"))
        self.assertTrue(result.accepted)
        self.assertEqual((result.season, result.episode), (0, 1))

    def test_explicit_season_absolute_episode_is_remapped(self):
        catalog = TitleCatalog({"slime": "史莱姆"}, {"史莱姆": [24, 24, 24, 24]})
        with ResolutionCache(self.config().cache_path) as cache:
            result = Resolver(catalog, self.config(), cache).resolve(self.media("Slime 4th Season - 87.mkv"))
        self.assertEqual((result.season, result.episode), (4, 15))

    def test_romanized_alias_uses_canonical_title_season_layout(self):
        catalog = TitleCatalog(
            {"himesamagoumonnojikandesu": "公主殿下，“拷问”的时间到了"},
            {"公主殿下，“拷问”的时间到了": [12, 12]},
        )
        with ResolutionCache(self.config().cache_path) as cache:
            result = Resolver(catalog, self.config(), cache).resolve(self.media("Hime-sama Goumon no Jikan desu [23].mkv"))
        self.assertEqual(result.canonical_title, "公主殿下，“拷问”的时间到了")
        self.assertEqual((result.season, result.episode), (2, 11))

    def test_catalog_change_invalidates_cached_decision(self):
        media = self.media("Example S01E01.mkv")
        with ResolutionCache(self.config().cache_path) as cache:
            first = Resolver(TitleCatalog({"example": "旧标题"}, {}), self.config(), cache).resolve(media)
            second = Resolver(TitleCatalog({"example": "新标题"}, {}), self.config(), cache).resolve(media)
        self.assertEqual(first.canonical_title, "旧标题")
        self.assertEqual(second.canonical_title, "新标题")

    def test_same_named_files_in_different_subdirectories_do_not_share_cache(self):
        left_path = self.root / "season" / "left" / "Episode01.mkv"
        right_path = self.root / "season" / "right" / "Episode01.mkv"
        left_path.parent.mkdir(parents=True)
        right_path.parent.mkdir(parents=True)
        left_path.write_bytes(b"same")
        right_path.write_bytes(b"same")
        shared_mtime = left_path.stat().st_mtime_ns
        os.utime(str(right_path), ns=(shared_mtime, shared_mtime))
        left = MediaFile(left_path, self.root, "season", "season/left/Episode01.mkv", 4, shared_mtime)
        right = MediaFile(right_path, self.root, "season", "season/right/Episode01.mkv", 4, shared_mtime)
        self.assertNotEqual(fingerprint(left, "rules"), fingerprint(right, "rules"))


if __name__ == "__main__":
    unittest.main()
