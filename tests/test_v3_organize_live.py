import os
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("AUTOANIME_LIVE_DOWNLOADS")
    and os.environ.get("AUTOANIME_LIVE_LIBRARY"),
    "set AUTOANIME_LIVE_DOWNLOADS and AUTOANIME_LIVE_LIBRARY to run live organize checks",
)
class LiveOrganizeTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(os.environ["AUTOANIME_LIVE_DOWNLOADS"]).expanduser().resolve()
        self.library = Path(os.environ["AUTOANIME_LIVE_LIBRARY"]).expanduser().resolve()
        if not self.source.is_dir():
            self.skipTest("AUTOANIME_LIVE_DOWNLOADS is not an existing directory")
        test_root = Path("F:/test").resolve()
        if self.library == Path("F:/动漫库").resolve() or not self.library.is_relative_to(test_root):
            self.skipTest("refusing live test library outside F:/test")
        self.library.mkdir(parents=True, exist_ok=True)

    def test_scoped_scan_is_non_destructive_and_plan_is_library_scoped(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        candidates = [path for path in sorted(self.source.iterdir()) if path.is_dir()][:8]
        if not candidates:
            self.skipTest("no child folders available for scoped live scan")
        scope = next((path for path in candidates if any(path.rglob("*"))), None)
        if scope is None:
            self.skipTest("no non-empty child folder available for scoped live scan")
        source_files = {path.resolve() for path in scope.rglob("*") if path.is_file()}

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "live.sqlite3"
            roots = RootService(database)
            source_root = roots.create_root("source", self.source)
            library_root = roots.create_root("library", self.library)
            profile = ProfileService(database).create_profile(
                CreateProfile(
                    name="live-organize",
                    source_root_id=source_root.id,
                    library_root_id=library_root.id,
                    mode="link",
                    execution_policy="review_all",
                    min_confidence=80,
                )
            )
            outcome = ScanService(database).run(profile.id, scope_paths=[scope])

            self.assertTrue(source_files.issubset({path.resolve() for path in scope.rglob("*") if path.is_file()}))
            from autoanime_v3.services.plans import PlanService

            plan = PlanService(database).get(outcome.plan_id)
            for item in plan.items:
                self.assertTrue(Path(item.destination_path).resolve().is_relative_to(self.library))

    def _small_scoped_video(self):
        videos = [
            path
            for path in self.source.iterdir()
            if path.is_file() and path.suffix.lower() in {".mkv", ".mp4", ".m4v", ".ts"}
        ]
        videos.sort(key=lambda path: path.stat().st_size)
        if not videos:
            self.skipTest("no root-level video files available for scoped live execute")
        chosen = videos[0]
        if chosen.stat().st_size > 80 * 1024 * 1024:
            self.skipTest("smallest live video is too large for execute/rollback coverage")
        return chosen

    def test_scoped_auto_apply_hardlink_execute_and_rollback(self):
        import os
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.agent_chat import AgentChatService
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        scope = self._small_scoped_video()
        source_path = scope.resolve()

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            database = Path(temporary) / "live.sqlite3"
            roots = RootService(database)
            source_root = roots.create_root("source", self.source)
            library_root = roots.create_root("library", self.library)
            profile = ProfileService(database).create_profile(
                CreateProfile(
                    name="live-auto-apply",
                    source_root_id=source_root.id,
                    library_root_id=library_root.id,
                    mode="link",
                    execution_policy="auto_apply_safe",
                    min_confidence=80,
                )
            )
            outcome = ScanService(database).run(profile.id, scope_paths=[scope])
            self.assertTrue(source_path.is_file())

            plans = PlanService(database)
            plan = plans.get(outcome.plan_id)
            for item in plan.items:
                self.assertTrue(Path(item.destination_path).resolve().is_relative_to(self.library))

            if outcome.review_count:
                review = ReviewService(database).list_open()[0]
                chat = AgentChatService(
                    database,
                    chat_completion=lambda unused_messages: (
                        '{"title":"拉拉熊","media_type":"episode","season":1,"episode":13,'
                        '"destination":"D:/invented","action":"move","reason":"live chat"}'
                    ),
                )
                session = chat.open_session("review", review.id)
                updated = chat.add_message(session["id"], "确认标题")
                self.assertNotIn("destination", updated.get("proposal") or {})
                applied = chat.apply(session["id"])
                self.assertTrue(applied["applied"])
                plan = plans.get(applied["result"]["id"])

            if plan.status != "approved":
                self.skipTest("scoped live scan did not produce an approved safe plan")
            executable = [
                item
                for item in plan.items
                if item.action not in {"skip", "conflict"} and item.decision != "rejected"
            ]
            if not executable:
                self.skipTest("approved live plan has no executable items")
            occupied = [item for item in executable if Path(item.destination_path).exists()]
            if occupied:
                self.skipTest("live library destination is already occupied")

            operations = OperationService(database, Path(temporary) / "operations")
            batch = operations.execute(plan.id)
            destination = Path(executable[0].destination_path)
            try:
                self.assertEqual(batch.status, "completed")
                self.assertTrue(destination.is_file())
                self.assertTrue(destination.resolve().is_relative_to(self.library.resolve()))
                self.assertTrue(os.path.samefile(source_path, destination))
                self.assertTrue(source_path.is_file())
            finally:
                operations.rollback(batch.id)
                for parent in [destination.parent, destination.parent.parent]:
                    if parent.is_dir() and parent.resolve().is_relative_to(self.library.resolve()) and parent != self.library.resolve() and not any(parent.iterdir()):
                        parent.rmdir()
            self.assertFalse(destination.exists())
            self.assertTrue(source_path.is_file())


@unittest.skipUnless(
    os.environ.get("AUTOANIME_LIVE_DOWNLOADS")
    and os.environ.get("AUTOANIME_LIVE_LIBRARY")
    and os.environ.get("AUTOANIME_LIVE_OPENAI_KEY"),
    "set AUTOANIME_LIVE_DOWNLOADS, AUTOANIME_LIVE_LIBRARY and AUTOANIME_LIVE_OPENAI_KEY",
)
class LiveOpenAIOrganizeTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(os.environ["AUTOANIME_LIVE_DOWNLOADS"]).expanduser().resolve()
        self.library = Path(os.environ["AUTOANIME_LIVE_LIBRARY"]).expanduser().resolve()
        if not self.source.is_dir():
            self.skipTest("AUTOANIME_LIVE_DOWNLOADS is not an existing directory")
        test_root = Path("F:/test").resolve()
        if self.library == Path("F:/动漫库").resolve() or not self.library.is_relative_to(test_root):
            self.skipTest("refusing live test library outside F:/test")
        if "ProgramData" in str(self.library) or str(self.library).startswith("C:\\ProgramData"):
            self.skipTest("refusing ProgramData library")
        self.library.mkdir(parents=True, exist_ok=True)
        self.api_key = os.environ["AUTOANIME_LIVE_OPENAI_KEY"].strip()
        self.base_url = os.environ.get("AUTOANIME_LIVE_OPENAI_BASE", "https://api.ymxc.asia").strip()
        self.model = os.environ.get("AUTOANIME_LIVE_OPENAI_MODEL", "deepseek-v4-flash").strip()

    def _cjk_sample(self):
        videos = [
            path
            for path in self.source.iterdir()
            if path.is_file() and path.suffix.lower() in {".mkv", ".mp4", ".m4v", ".ts"}
        ]
        named = [path for path in videos if "拉拉熊" in path.name]
        chosen = named[0] if named else min(videos, key=lambda path: path.stat().st_size) if videos else None
        if chosen is None:
            self.skipTest("no root-level video for live OpenAI organize")
        if chosen.stat().st_size > 80 * 1024 * 1024:
            self.skipTest("live OpenAI sample is too large to execute")
        return chosen

    def _enable_openai(self, database):
        from autoanime_v3.security.secrets import DpapiSecretStore
        from autoanime_v3.services.auth import SecretService
        from autoanime_v3.services.settings import (
            OPENAI_API_KEY_SECRET,
            OPENAI_BASE_URL_KEY,
            OPENAI_ENABLED_KEY,
            OPENAI_MODEL_KEY,
            OPENAI_TIMEOUT_KEY,
            PARSE_AGENT_MODE_KEY,
            SettingsService,
        )

        settings = SettingsService(database)
        settings.update(OPENAI_ENABLED_KEY, True, 1)
        settings.update(OPENAI_BASE_URL_KEY, self.base_url, 1)
        settings.update(OPENAI_MODEL_KEY, self.model, 1)
        settings.update(OPENAI_TIMEOUT_KEY, 60, 1)
        settings.update(PARSE_AGENT_MODE_KEY, "all", 1)
        SecretService(database, DpapiSecretStore()).set_secret(OPENAI_API_KEY_SECRET, self.api_key)

    def test_real_openai_identify_hardlink_and_rollback(self):
        import os as os_module
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        scope = self._cjk_sample()
        source_path = scope.resolve()
        forbidden_library = Path("F:/动漫库").resolve()
        forbidden_data = Path("C:/ProgramData/AutoAnime")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, dir="F:/test") as temporary:
            data_dir = Path(temporary)
            self.assertFalse(str(data_dir.resolve()).startswith(str(forbidden_data)))
            database = data_dir / "live-openai.sqlite3"
            self._enable_openai(database)
            roots = RootService(database)
            source_root = roots.create_root("source", self.source)
            library_root = roots.create_root("library", self.library)
            profile = ProfileService(database).create_profile(
                CreateProfile(
                    name="live-openai-organize",
                    source_root_id=source_root.id,
                    library_root_id=library_root.id,
                    mode="link",
                    execution_policy="auto_apply_safe",
                    min_confidence=80,
                )
            )
            outcome = ScanService(database).run(profile.id, scope_paths=[scope])
            self.assertTrue(source_path.is_file())
            plans = PlanService(database)
            plan = plans.get(outcome.plan_id)
            for item in plan.items:
                destination = Path(item.destination_path).resolve()
                self.assertTrue(destination.is_relative_to(self.library))
                self.assertFalse(destination.is_relative_to(forbidden_library))

            if outcome.review_count:
                open_reviews = ReviewService(database).list_open()
                self.assertTrue(open_reviews)
                self.fail(
                    "live OpenAI left %s open review(s); first payload=%s"
                    % (len(open_reviews), getattr(open_reviews[0], "payload", open_reviews[0]))
                )

            if plan.status != "approved":
                self.fail("live OpenAI plan status is %s, expected approved" % plan.status)
            executable = [
                item
                for item in plan.items
                if item.action not in {"skip", "conflict"} and item.decision != "rejected"
            ]
            self.assertTrue(executable, "live OpenAI plan has no executable items")
            occupied = [item for item in executable if Path(item.destination_path).exists()]
            if occupied:
                self.skipTest("live library destination is already occupied")

            operations = OperationService(database, data_dir / "operations")
            batch = operations.execute(plan.id)
            destination = Path(executable[0].destination_path)
            try:
                self.assertEqual(batch.status, "completed")
                self.assertTrue(destination.is_file())
                self.assertTrue(destination.resolve().is_relative_to(self.library.resolve()))
                self.assertTrue(os_module.path.samefile(source_path, destination))
                self.assertTrue(source_path.is_file())
            finally:
                operations.rollback(batch.id)
                for parent in [destination.parent, destination.parent.parent]:
                    if (
                        parent.is_dir()
                        and parent.resolve().is_relative_to(self.library.resolve())
                        and parent != self.library.resolve()
                        and not any(parent.iterdir())
                    ):
                        parent.rmdir()
            self.assertFalse(destination.exists())
            self.assertTrue(source_path.is_file())


if __name__ == "__main__":
    unittest.main()
