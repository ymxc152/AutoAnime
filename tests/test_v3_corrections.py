import sqlite3
import tempfile
import unittest
from pathlib import Path


class CorrectionServiceTests(unittest.TestCase):
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
                name="纠正测试",
                source_root_id=self.source_root.id,
                library_root_id=self.library_root.id,
                mode="link",
            )
        )
        self.operation_dir = self.root / "operations"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def organize(self, files):
        """Scan + wholesale approve + execute a batch of {filename: bytes}."""
        from autoanime_v3.services.operations import OperationService
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.scans import ScanService

        for name, content in files.items():
            (self.source / name).write_bytes(content)
        outcome = ScanService(self.database).run(self.profile.id)
        plan = PlanService(self.database).approve(outcome.plan_id)
        OperationService(self.database, self.operation_dir).execute(plan.id)
        return outcome

    def change_request(self, show_id, base_revision, new_title, reason="E2E 人工纠正"):
        from autoanime_v3.services.changes import ChangeService

        return ChangeService(self.database).preview_show_change(
            show_id,
            base_revision,
            {"canonical_title": new_title, "title_locked": False},
            reason,
        )

    def last_correction_batch(self):
        connection = sqlite3.connect(str(self.database))
        try:
            return connection.execute(
                "SELECT id FROM operation_batches WHERE kind = 'correction' ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()

    def show_row(self, canonical_title):
        connection = sqlite3.connect(str(self.database))
        try:
            return connection.execute(
                "SELECT id, canonical_title, revision FROM shows WHERE canonical_title = ?",
                (canonical_title,),
            ).fetchone()
        finally:
            connection.close()

    def test_rename_moves_files_and_updates_paths(self):
        from autoanime_v3.services.corrections import CorrectionService

        self.organize({"测试番A S01E01.mkv": b"payload-A" * 200})
        show = self.show_row("测试番A")
        request = self.change_request(show[0], show[2], "测试番A2")

        updated = CorrectionService(self.database, self.operation_dir).apply(request.id)

        self.assertEqual(updated.canonical_title, "测试番A2")
        remaining = list((self.library / "测试番A").rglob("*")) if (self.library / "测试番A").exists() else []
        self.assertEqual([p for p in remaining if p.is_file()], [])
        new_files = [p for p in self.library.rglob("*") if p.is_file()]
        self.assertEqual(len(new_files), 1)
        self.assertIn("测试番A2", str(new_files[0]))
        connection = sqlite3.connect(str(self.database))
        try:
            location = connection.execute(
                "SELECT path FROM file_locations WHERE role = 'library' AND state = 'present'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn("测试番A2", location)

    def test_rename_rollback_restores_files_and_title(self):
        from autoanime_v3.services.corrections import CorrectionService
        from autoanime_v3.services.operations import OperationService

        self.organize({"测试番A S01E01.mkv": b"payload-A" * 200})
        show = self.show_row("测试番A")
        request = self.change_request(show[0], show[2], "测试番A2")
        CorrectionService(self.database, self.operation_dir).apply(request.id)
        self.assertEqual([p for p in self.library.rglob("*") if p.is_file()][0].parent.parent.name, "测试番A2")

        OperationService(self.database, self.operation_dir).rollback(self.last_correction_batch())

        restored = [p for p in self.library.rglob("*") if p.is_file()]
        self.assertEqual(len(restored), 1)
        self.assertIn("测试番A", str(restored[0]))
        self.assertEqual(self.show_row("测试番A") is not None, True)
        connection = sqlite3.connect(str(self.database))
        try:
            location = connection.execute(
                "SELECT path FROM file_locations WHERE role = 'library' AND state = 'present'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn("测试番A", location)

    def test_merge_moves_files_and_reparents_database(self):
        from autoanime_v3.services.corrections import CorrectionService

        self.organize(
            {
                "测试番A S01E01.mkv": b"payload-A" * 200,
                "测试番B S01E02.mkv": b"payload-B" * 200,
            }
        )
        show_b = self.show_row("测试番B")
        request = self.change_request(show_b[0], show_b[2], "测试番A")

        updated = CorrectionService(self.database, self.operation_dir).apply(request.id)

        self.assertEqual(updated.canonical_title, "测试番A")
        connection = sqlite3.connect(str(self.database))
        try:
            show_count = connection.execute("SELECT COUNT(*) FROM shows").fetchone()[0]
            assignment_shows = connection.execute(
                "SELECT DISTINCT show_id FROM media_assignments"
            ).fetchall()
            episodes = connection.execute(
                "SELECT e.episode_number FROM episodes e JOIN seasons s ON s.id = e.season_id JOIN shows sh ON sh.id = s.show_id WHERE sh.canonical_title = '测试番A' ORDER BY e.episode_number"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(show_count, 1)
        self.assertEqual(len(assignment_shows), 1)
        self.assertEqual({row[0] for row in episodes}, {"1", "2"})
        files = [p for p in self.library.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 2)
        self.assertTrue(all("测试番A" in str(p) for p in files))

    def test_merge_conflict_keeps_larger_file(self):
        from autoanime_v3.services.corrections import CorrectionService

        self.organize(
            {
                "测试番A S01E01.mkv": b"BIG" * 4000,
                "测试番B S01E01.mkv": b"small" * 10,
            }
        )
        show_b = self.show_row("测试番B")
        request = self.change_request(show_b[0], show_b[2], "测试番A")

        CorrectionService(self.database, self.operation_dir).apply(request.id)

        files = [p for p in self.library.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 1)  # the larger one survives
        self.assertGreater(files[0].stat().st_size, 100)
        connection = sqlite3.connect(str(self.database))
        try:
            show_count = connection.execute("SELECT COUNT(*) FROM shows").fetchone()[0]
            assignments = connection.execute(
                "SELECT COUNT(*) FROM media_assignments"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(show_count, 1)
        self.assertEqual(assignments, 1)
        # The discarded smaller file is parked (not deleted) for rollback.
        trash = self.operation_dir / "trash"
        self.assertTrue(any(p.is_file() for p in trash.rglob("*") if p.is_file()))

    def test_merge_rollback_restores_both_shows_and_files(self):
        from autoanime_v3.services.corrections import CorrectionService
        from autoanime_v3.services.operations import OperationService

        self.organize(
            {
                "测试番A S01E01.mkv": b"payload-A" * 200,
                "测试番B S01E02.mkv": b"payload-B" * 200,
            }
        )
        show_b = self.show_row("测试番B")
        request = self.change_request(show_b[0], show_b[2], "测试番A")
        CorrectionService(self.database, self.operation_dir).apply(request.id)
        self.assertEqual(self.show_row("测试番B"), None)

        OperationService(self.database, self.operation_dir).rollback(self.last_correction_batch())

        self.assertIsNotNone(self.show_row("测试番A"))
        self.assertIsNotNone(self.show_row("测试番B"))
        files = [p for p in self.library.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 2)
        self.assertTrue(any("测试番A" in str(p) for p in files))
        self.assertTrue(any("测试番B" in str(p) for p in files))


    def test_backfill_library_creates_shows_for_prior_executions(self):
        from autoanime_v3.services.corrections import CorrectionService

        self.organize({"测试番A S01E01.mkv": b"payload-A" * 200})
        # Simulate a pre-shows-sync execution: library entities are missing.
        connection = sqlite3.connect(str(self.database))
        try:
            connection.execute("DELETE FROM media_assignments")
            connection.execute("DELETE FROM episodes")
            connection.execute("DELETE FROM seasons")
            connection.execute("DELETE FROM shows")
            connection.commit()
        finally:
            connection.close()

        created = CorrectionService(self.database, self.operation_dir).backfill_library()

        self.assertEqual(created, 1)
        self.assertIsNotNone(self.show_row("测试番A"))
        connection = sqlite3.connect(str(self.database))
        try:
            assignments = connection.execute(
                "SELECT COUNT(*) FROM media_assignments"
            ).fetchone()[0]
            location = connection.execute(
                "SELECT path FROM file_locations WHERE role = 'library' AND state = 'present'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(assignments, 1)
        self.assertIn("测试番A", location)
        # Idempotent: a second pass creates nothing new.
        again = CorrectionService(self.database, self.operation_dir).backfill_library()
        self.assertEqual(again, 0)


if __name__ == "__main__":
    unittest.main()
