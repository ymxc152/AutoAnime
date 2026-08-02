import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoanime_v3.cache import ResolutionCache, fingerprint
from autoanime_v3.executor import ExecutionError, execute_plan, rollback
from autoanime_v3.models import MediaFile, Resolution
from autoanime_v3.planner import build_plan


class PlannerExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def resolution(self, name, tag):
        path = self.root / name
        path.write_bytes(name.encode("utf-8"))
        stat = path.stat()
        media = MediaFile(path, self.root, "bundle", name, stat.st_size, stat.st_mtime_ns)
        return Resolution(media, "测试番剧", 1, 3, False, 0.99, True, tag)

    def test_duplicate_platform_versions_do_not_overwrite(self):
        left = self.resolution("Show.S01E03.Baha.mkv", "Baha")
        right = self.resolution("Show.S01E03.friDay.mkv", "friDay")
        plan = build_plan([left, right], self.root / "library")
        organized = [entry for entry in plan if entry.action == "organize"]
        skipped = [entry for entry in plan if entry.action == "skip"]
        self.assertEqual(len(organized), 1)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].reason, "not_preferred_release")
        self.assertNotEqual(organized[0].destination, skipped[0].destination)

    def test_move_and_rollback(self):
        resolution = self.resolution("Show.S01E03.mkv", "")
        plan = build_plan([resolution], self.root / "library")
        with ResolutionCache(self.root / "library.sqlite3") as cache:
            cache.put(resolution)
            log = execute_plan(plan, "move", True, cache, self.root / "logs")
            destination = plan[0].destination
            self.assertTrue(destination and destination.exists() and not resolution.media.path.exists())
            self.assertEqual(rollback(log, cache), 1)
            row = cache.connection.execute(
                "SELECT current_path, status FROM media_files WHERE source_key IS NOT NULL"
            ).fetchone()
            self.assertEqual(row["current_path"], str(resolution.media.path))
            self.assertEqual(row["status"], "identified")
        self.assertTrue(resolution.media.path.exists())
        self.assertFalse(destination.exists())

    def test_subtitle_language_suffix_is_preserved_case_insensitively(self):
        resolution = self.resolution("Show.S01E03.mkv", "")
        subtitle = self.root / "SHOW.S01E03.CHS.ass"
        subtitle.write_text("subtitle", encoding="utf-8")
        plan = build_plan([resolution], self.root / "library")
        subtitle_entries = [entry for entry in plan if entry.companion_of]
        self.assertEqual(len(subtitle_entries), 1)
        self.assertTrue(subtitle_entries[0].destination.name.endswith(".CHS.ass"))

    def test_failed_batch_auto_rolls_back_completed_moves(self):
        first = self.resolution("Show.S01E03.mkv", "")
        second = self.resolution("Show.S01E04.mkv", "")
        second.episode = 4
        plan = build_plan([first, second], self.root / "library")
        second.media.path.unlink()
        with ResolutionCache(self.root / "library.sqlite3") as cache:
            with self.assertRaises(ExecutionError):
                execute_plan(plan, "move", True, cache, self.root / "logs")
        self.assertTrue(first.media.path.exists())
        self.assertFalse(plan[0].destination.exists())

    def test_failed_copy_removes_partial_destination(self):
        resolution = self.resolution("Show.S01E03.mkv", "")
        plan = build_plan([resolution], self.root / "library")

        def fail_after_partial_copy(source_handle, destination_handle, length):
            destination_handle.write(source_handle.read(1))
            raise OSError("simulated copy failure")

        with mock.patch("autoanime_v3.executor.shutil.copyfileobj", side_effect=fail_after_partial_copy):
            with ResolutionCache(self.root / "library.sqlite3") as cache:
                cache.put(resolution)
                with self.assertRaises(ExecutionError):
                    execute_plan(plan, "copy", True, cache, self.root / "logs")
        self.assertTrue(resolution.media.path.exists())
        self.assertFalse(plan[0].destination.exists())

    def test_manual_rollback_refuses_to_delete_changed_copy(self):
        resolution = self.resolution("Show.S01E03.mkv", "")
        plan = build_plan([resolution], self.root / "library")
        with ResolutionCache(self.root / "library.sqlite3") as cache:
            cache.put(resolution)
            log = execute_plan(plan, "copy", True, cache, self.root / "logs")
            plan[0].destination.write_bytes(b"changed after organization")
            with self.assertRaisesRegex(ExecutionError, "已变化|摘要"):
                rollback(log, cache)
        self.assertTrue(resolution.media.path.exists())
        self.assertTrue(plan[0].destination.exists())

    def test_hard_link_and_rollback_keep_original_source(self):
        resolution = self.resolution("Show.S01E03.mkv", "")
        plan = build_plan([resolution], self.root / "library")
        with ResolutionCache(self.root / "library.sqlite3") as cache:
            cache.put(resolution)
            log = execute_plan(plan, "link", True, cache, self.root / "logs")
            destination = plan[0].destination
            self.assertTrue(destination.exists() and destination.samefile(resolution.media.path))
            self.assertEqual(rollback(log, cache), 1)
        self.assertTrue(resolution.media.path.exists())
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
