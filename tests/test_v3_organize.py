import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoanime_v3.cache import ResolutionCache
from autoanime_v3.catalog import TitleCatalog
from autoanime_v3.config import AppConfig
from autoanime_v3.models import Evidence, MediaFile, Resolution
from autoanime_v3.organize import OrganizeAgent
from autoanime_v3.services.memory import ShowMemoryService


class OrganizeAgentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "downloads"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()
        self.database = self.root / "web.sqlite3"
        self.alias_file = self.root / "aliases.json"
        self.alias_file.write_text("{}", encoding="utf-8")
        self.config = AppConfig(
            self.database,
            self.alias_file,
            min_confidence=0.80,
            output_root=self.library,
            openai_enabled=False,
        )
        self.memory = ShowMemoryService(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def media(self, name, folder="番組"):
        directory = self.source / folder
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(b"video")
        stat = path.stat()
        return MediaFile(
            path,
            self.source,
            folder,
            str(path.relative_to(self.source)).replace("\\", "/"),
            stat.st_size,
            stat.st_mtime_ns,
        )

    def agent(self):
        return OrganizeAgent(
            TitleCatalog({}, {}),
            self.config,
            ResolutionCache(self.root / "resolver-cache.sqlite3").__enter__(),
            self.memory,
        )

    def test_run_groups_cjk_episodes_remembers_and_plans_only_under_library(self):
        files = [
            self.media("葬送的芙莉莲 S01E01.mkv"),
            self.media("葬送的芙莉莲 S01E02.mkv"),
        ]
        agent = self.agent()
        try:
            resolutions, plan = agent.run(files, self.source, self.library)
        finally:
            agent.cache.__exit__(None, None, None)

        self.assertEqual(len(resolutions), 2)
        self.assertTrue(all(item.accepted for item in resolutions))
        self.assertEqual({item.canonical_title for item in resolutions}, {"葬送的芙莉莲"})
        self.assertTrue(plan)
        destinations = [entry.destination for entry in plan if entry.destination is not None]
        self.assertTrue(destinations)
        library_path = self.library.resolve()
        self.assertTrue(all(path.resolve().is_relative_to(library_path) for path in destinations))
        learned = self.memory.list()
        self.assertTrue(learned)
        self.assertEqual({row["canonical_title"] for row in learned}, {"葬送的芙莉莲"})

    def test_started_and_unit_callbacks_include_counts_folder_and_title(self):
        files = [self.media("孤独摇滚 S01E01.mkv"), self.media("孤独摇滚 S01E02.mkv")]
        started = []
        units = []
        agent = self.agent()
        try:
            agent.run(
                files,
                self.source,
                self.library,
                on_started=started.append,
                on_unit=units.append,
            )
        finally:
            agent.cache.__exit__(None, None, None)

        self.assertEqual(started, [{"units": 1, "files": 2}])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["folder"], str(self.source / "番組"))
        self.assertEqual(units[0]["files"], 2)
        self.assertEqual(units[0]["title"], "孤独摇滚")
        self.assertTrue(units[0]["accepted"])

    def test_split_results_do_not_learn_split_titles(self):
        files = [self.media("混合文件 S01E01.mkv"), self.media("混合文件 S01E02.mkv")]
        resolutions = [
            Resolution(
                media=file,
                canonical_title=title,
                season=1,
                episode=index,
                confidence=0.99,
                accepted=True,
                evidence=[Evidence("identify_batch", title, 0.99)],
                warnings=["identify_split"],
            )
            for index, (file, title) in enumerate(zip(files, ("甲番", "乙番")), 1)
        ]
        agent = self.agent()
        with mock.patch.object(agent.resolver, "resolve_unit", return_value=resolutions):
            try:
                result, plan = agent.run(files, self.source, self.library)
            finally:
                agent.cache.__exit__(None, None, None)

        self.assertEqual(result, resolutions)
        self.assertEqual(plan, [mock.ANY, mock.ANY])
        self.assertEqual(self.memory.list(), [])

    def test_rejected_resolution_is_not_remembered(self):
        file = self.media("待确认 S01E01.mkv")
        resolution = Resolution(
            media=file,
            canonical_title="待确认",
            season=1,
            episode=1,
            confidence=0.2,
            accepted=False,
        )
        agent = self.agent()
        with mock.patch.object(agent.resolver, "resolve_unit", return_value=[resolution]):
            try:
                agent.run([file], self.source, self.library)
            finally:
                agent.cache.__exit__(None, None, None)
        self.assertEqual(self.memory.list(), [])


class ScanServiceCallbackTests(unittest.TestCase):
    def test_scan_service_forwards_scoped_adapter_callbacks(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "web.sqlite3"
            source = root / "source"
            library = root / "library"
            scoped = source / "small"
            scoped.mkdir(parents=True)
            library.mkdir()
            roots = RootService(database)
            source_root = roots.create_root("source", source)
            library_root = roots.create_root("library", library)
            profile = ProfileService(database).create_profile(
                CreateProfile(
                    name="callbacks",
                    source_root_id=source_root.id,
                    library_root_id=library_root.id,
                    mode="link",
                    execution_policy="review_all",
                )
            )

            class Adapter:
                def __init__(self):
                    self.started = []
                    self.units = []

                def analyze_scoped(self, source, library, min_confidence, scope_paths, on_unit=None, on_started=None):
                    if on_started:
                        on_started({"units": 1, "files": 1})
                    if on_unit:
                        on_unit({"folder": str(scoped), "files": 1, "title": "测试番"})
                    self.started.append((source, library, min_confidence, scope_paths))
                    return "rules", [], []

            adapter = Adapter()
            outcome = ScanService(database, adapter=adapter).run(
                profile.id,
                scope_paths=[scoped],
                on_started=adapter.started.append,
                on_unit=adapter.units.append,
            )
            self.assertEqual(outcome.discovered_count, 0)
            self.assertEqual(len(adapter.started), 2)
            self.assertEqual(adapter.started[1][3], [scoped.resolve()])
            self.assertEqual(len(adapter.units), 1)
            self.assertEqual(adapter.units[0]["title"], "测试番")


class WebhookAliasTests(unittest.TestCase):
    def test_downloader_hook_symbols_when_available(self):
        try:
            from autoanime_v3.api.app import DownloaderHookBody, collect_hook_paths
        except ImportError:
            self.skipTest("collect_hook_paths is not available yet")
        body = DownloaderHookBody(path="one.mkv", paths=["two.mkv"])
        self.assertEqual(collect_hook_paths(body), ["two.mkv", "one.mkv"])
        aliased = DownloaderHookBody.model_validate(
            {"savePath": "from-qb.mkv", "content_path": "content.mkv", "folder": "show", "foo": 1}
        )
        self.assertEqual(
            collect_hook_paths(aliased),
            ["from-qb.mkv", "content.mkv", "show"],
        )


if __name__ == "__main__":
    unittest.main()
