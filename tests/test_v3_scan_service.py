import sqlite3
import tempfile
import unittest
from pathlib import Path


class ScanServiceTests(unittest.TestCase):
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
        self.source_root = roots.create_root("source", self.source)
        self.library_root = roots.create_root("library", self.library)
        self.profile = ProfileService(self.database).create_profile(
            CreateProfile(
                name="默认整理",
                source_root_id=self.source_root.id,
                library_root_id=self.library_root.id,
                min_confidence=86,
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_scan_records_facts_reviews_and_draft_plan_without_touching_library(self):
        (self.source / "测试番 S01E01.mkv").write_bytes(b"safe-media")
        (self.source / "Unknown Show - 02.mkv").write_bytes(b"needs-review")

        from autoanime_v3.services.scans import ScanService

        outcome = ScanService(self.database).run(self.profile.id)

        self.assertEqual(outcome.discovered_count, 2)
        self.assertEqual(outcome.review_count, 1)
        self.assertEqual(outcome.plan_status, "draft")
        self.assertEqual(list(self.library.rglob("*")), [])
        connection = sqlite3.connect(str(self.database))
        try:
            counts = {
                name: connection.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
                for name in [
                    "scan_runs",
                    "scan_items",
                    "media_files",
                    "identification_results",
                    "review_items",
                    "plans",
                ]
            }
        finally:
            connection.close()
        self.assertEqual(counts["scan_runs"], 1)
        self.assertEqual(counts["scan_items"], 2)
        self.assertEqual(counts["media_files"], 2)
        self.assertEqual(counts["identification_results"], 2)
        self.assertEqual(counts["review_items"], 1)
        self.assertEqual(counts["plans"], 1)


if __name__ == "__main__":
    unittest.main()

