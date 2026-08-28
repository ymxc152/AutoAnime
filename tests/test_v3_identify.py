import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoanime_v3.cache import ResolutionCache
from autoanime_v3.catalog import TitleCatalog
from autoanime_v3.config import AppConfig
from autoanime_v3.identify_units import IdentifyUnit, group_work_units
from autoanime_v3.models import MediaFile
from autoanime_v3.resolver import Resolver


def _fake_response(content):
    payload = {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}
    data = json.dumps(payload).encode("utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return data

    return _Response()


class IdentifyGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "downloads"
        self.source.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def media(self, relative, context=""):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        stat = path.stat()
        return MediaFile(
            path,
            self.source,
            context or path.parent.name,
            str(path.relative_to(self.source)).replace("\\", "/"),
            stat.st_size,
            stat.st_mtime_ns,
        )

    def test_mixed_folder_splits_by_cjk_title(self):
        files = [
            self.media("mix/芙莉莲 S01E01.mkv", "mix"),
            self.media("mix/芙莉莲 S01E02.mkv", "mix"),
            self.media("mix/孤独摇滚 S01E01.mkv", "mix"),
        ]
        units = group_work_units(files, TitleCatalog({}, {}), self.source)
        titles = sorted(unit.hint_title for unit in units)
        self.assertEqual(len(units), 2)
        self.assertIn("芙莉莲", titles)
        self.assertIn("孤独摇滚", titles)

    def test_generic_dump_does_not_collapse_two_shows(self):
        files = [
            self.media("测试番 S01E01.mkv", "downloads"),
            self.media("另一部 S01E01.mkv", "downloads"),
        ]
        units = group_work_units(files, TitleCatalog({}, {}), self.source)
        self.assertEqual(len(units), 2)

    def test_nested_season_folder_is_grouped_by_parent(self):
        files = [
            self.media("Show/Season 02/测试番 S02E01.mkv", "Show"),
            self.media("Show/Season 02/测试番 S02E02.mkv", "Show"),
            self.media("Other/孤独摇滚 S01E01.mkv", "Other"),
        ]
        units = group_work_units(files, TitleCatalog({}, {}), self.source)
        parents = {str(unit.folder) for unit in units}
        self.assertEqual(len(units), 2)
        self.assertTrue(any(unit.folder.name == "Season 02" for unit in units))
        self.assertTrue(any(unit.folder.name == "Other" for unit in units))
        self.assertEqual(len(parents), 2)


class IdentifyResolverTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source = self.root / "downloads"
        self.source.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def media(self, relative, context=""):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        stat = path.stat()
        return MediaFile(
            path,
            self.source,
            context or path.parent.name,
            str(path.relative_to(self.source)).replace("\\", "/"),
            stat.st_size,
            stat.st_mtime_ns,
        )

    def config(self, **overrides):
        kwargs = {
            "openai_enabled": False,
            "openai_api_key": "",
            "parse_agent_mode": "off",
        }
        kwargs.update(overrides)
        return AppConfig(self.root / "library.sqlite3", self.root / "aliases.json", **kwargs)

    def test_machine_sibling_memory_stamps_untitled_file(self):
        files = [
            self.media("Show/测试番 S01E01.mkv", "Show"),
            self.media("Show/测试番 S01E02.mkv", "Show"),
            self.media("Show/S01E03.mkv", "Show"),
        ]
        unit = IdentifyUnit(folder=files[0].path.parent, files=tuple(files), hint_title="测试番")
        with ResolutionCache(self.config().cache_path) as cache:
            results = Resolver(TitleCatalog({}, {}), self.config(), cache).resolve_unit(unit)
        titles = {item.canonical_title for item in results}
        self.assertEqual(titles, {"测试番"})
        untitled = next(item for item in results if item.media.path.name == "S01E03.mkv")
        self.assertTrue(untitled.accepted)
        self.assertTrue(any(item.agent == "sibling" for item in untitled.evidence))

    def test_batch_identify_uses_one_llm_call_for_folder(self):
        files = [
            self.media("Show/Frieren S01E01.mkv", "Show"),
            self.media("Show/Frieren S01E02.mkv", "Show"),
            self.media("Show/Frieren S01E03.mkv", "Show"),
        ]
        unit = IdentifyUnit(folder=files[0].path.parent, files=tuple(files))
        config = self.config(
            openai_enabled=True,
            openai_api_key="k",
            openai_base_url="https://api.example.com",
            openai_model="gpt-4.1-mini",
            openai_timeout=30,
            parse_agent_mode="uncertain",
        )
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            with mock.patch(
                "autoanime_v3.identify.urllib.request.urlopen",
                return_value=_fake_response(
                    {
                        "title_zh": "葬送的芙莉莲",
                        "aliases": ["Frieren"],
                        "confidence": 0.95,
                        "reason": "folder siblings",
                        "split": False,
                    }
                ),
            ) as urlopen:
                results = resolver.resolve_unit(unit)
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(all(item.accepted for item in results))
        self.assertEqual({item.canonical_title for item in results}, {"葬送的芙莉莲"})
        self.assertTrue(any(item.agent == "identify_batch" for item in results[0].evidence))


class IdentifyMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "downloads"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService

        roots = RootService(self.database)
        source_root = roots.create_root("source", self.source)
        library_root = roots.create_root("library", self.library)
        self.profile = ProfileService(self.database).create_profile(
            CreateProfile(
                name="memory",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
                min_confidence=86,
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_learned_alias_does_not_change_rule_version(self):
        from autoanime_v3.services.memory import ShowMemoryService
        from autoanime_v3.services.rules import RuleService

        before = RuleService(self.database).get_active().content_hash
        ShowMemoryService(self.database).remember(["Frieren"], "葬送的芙莉莲", source="identify_batch")
        after = RuleService(self.database).get_active().content_hash
        self.assertEqual(before, after)
        overlay = ShowMemoryService(self.database).load_overlay()
        self.assertEqual(overlay["aliases"]["frieren"], "葬送的芙莉莲")

    def test_memory_drops_episode_tails_and_caps_batch_aliases(self):
        from autoanime_v3.services.memory import ShowMemoryService

        service = ShowMemoryService(self.database)
        service.remember(
            ["真实link测试 S01E01.mkv", "真实link测试 S01E02.mkv", "真实link测试"],
            "测试",
        )
        keys = {item["alias_key"] for item in service.list()}
        self.assertIn("真实link测试", keys)
        self.assertNotIn("测试", keys)
        self.assertTrue(all(not key.endswith("e01") and not key.endswith("e02") for key in keys))
        service.remember([f"AltName{index}" for index in range(20)], "测试")
        batch = [item for item in service.list() if item["canonical_title"] == "测试"]
        self.assertLessEqual(len(batch), 8)

    def test_learned_alias_accepts_new_filename_on_later_scan(self):
        from autoanime_v3.services.memory import ShowMemoryService
        from autoanime_v3.services.scans import ScanService

        ShowMemoryService(self.database).remember(["Frieren"], "葬送的芙莉莲")
        (self.source / "Frieren S01E01.mkv").write_bytes(b"video-file" * 64)
        outcome = ScanService(self.database).run(self.profile.id)
        self.assertEqual(outcome.review_count, 0)
        self.assertIn(outcome.plan_status, {"ready", "approved"})

    def test_review_resolve_writes_memory_for_next_scan(self):
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        (self.source / "Unknown Show S01E01.mkv").write_bytes(b"needs-review" * 64)
        ScanService(self.database).run(self.profile.id)
        review = ReviewService(self.database).list_open()[0]
        ReviewService(self.database).resolve(
            review.id,
            {"title": "人工确认番", "media_type": "episode", "season": 1, "episode": 1},
        )
        (self.source / "Unknown Show S01E02.mkv").write_bytes(b"second-file" * 64)
        second = ScanService(self.database).run(self.profile.id)
        self.assertEqual(second.review_count, 0)

    def test_auto_apply_safe_still_enqueues_when_folder_is_accepted(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.scans import ScanService

        profile = ProfileService(self.database).create_profile(
            CreateProfile(
                name="auto",
                source_root_id=self.profile.source_root_id
                if hasattr(self.profile, "source_root_id")
                else 1,
                library_root_id=self.profile.library_root_id
                if hasattr(self.profile, "library_root_id")
                else 2,
                execution_policy="auto_apply_safe",
                min_confidence=86,
            )
        )
        folder = self.source / "Show"
        folder.mkdir()
        (folder / "测试番 S01E01.mkv").write_bytes(b"safe-media" * 64)
        (folder / "测试番 S01E02.mkv").write_bytes(b"safe-media-2" * 64)
        outcome = ScanService(self.database).run(profile.id)
        self.assertEqual(outcome.review_count, 0)
        connection = sqlite3.connect(str(self.database))
        try:
            jobs = connection.execute(
                "SELECT job_type, status FROM jobs WHERE job_type = 'execute_plan'"
            ).fetchall()
            plan_status = connection.execute(
                "SELECT status FROM plans WHERE id = ?", (outcome.plan_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(plan_status, "approved")
        self.assertTrue(jobs)

    def test_open_review_still_blocks_auto_apply(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.scans import ScanService

        profile = ProfileService(self.database).create_profile(
            CreateProfile(
                name="blocked",
                source_root_id=1,
                library_root_id=2,
                execution_policy="auto_apply_safe",
                min_confidence=86,
            )
        )
        (self.source / "测试番 S01E01.mkv").write_bytes(b"safe-media" * 64)
        (self.source / "Unknown Show S01E02.mkv").write_bytes(b"needs-review" * 64)
        outcome = ScanService(self.database).run(profile.id)
        self.assertGreaterEqual(outcome.review_count, 1)
        self.assertNotEqual(outcome.plan_status, "approved")


if __name__ == "__main__":
    unittest.main()
