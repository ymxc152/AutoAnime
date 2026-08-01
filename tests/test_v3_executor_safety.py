import errno
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autoanime_v3.executor as executor
from autoanime_v3.cache import ResolutionCache
from autoanime_v3.executor import ExecutionError, ExecutionFailure, execute_plan, rollback
from autoanime_v3.models import MediaFile, PlanEntry, Resolution


class ExecutorSafetyTests(unittest.TestCase):
    def _entry(self, root, content=b"original-media", filename="episode.mkv"):
        source = root / "incoming" / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        stat = source.stat()
        media = MediaFile(
            source,
            source.parent,
            "incoming",
            source.name,
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )
        resolution = Resolution(media, "Test Show", 1, 1, False, 1.0, True)
        destination = root / "library" / "Test Show" / filename
        return PlanEntry(source, destination, "organize", resolution)

    def _execute(self, root, entry, mode):
        cache_path = root / "library.sqlite3"
        with ResolutionCache(cache_path) as cache:
            cache.put(entry.resolution)
            return execute_plan([entry], mode, True, cache, root / "logs")

    def test_partial_automatic_rollback_exposes_recovery_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._entry(root, b"first-media")
            second = self._entry(root, b"second-media", "episode-2.mkv")

            cache_path = root / "library.sqlite3"
            with ResolutionCache(cache_path) as cache:
                cache.put(first.resolution)
                cache.put(second.resolution)
                calls = {"count": 0}

                def apply_then_fail(entry, unused_mode):
                    calls["count"] += 1
                    if calls["count"] == 2:
                        raise OSError("apply failed")
                    entry.destination.parent.mkdir(parents=True, exist_ok=True)
                    entry.destination.write_bytes(entry.source.read_bytes())

                with mock.patch.object(
                    executor,
                    "_apply_one",
                    side_effect=apply_then_fail,
                ), mock.patch.object(
                    executor,
                    "_rollback_one",
                    side_effect=OSError("rollback failed"),
                ):
                    with self.assertRaises(ExecutionFailure) as raised:
                        execute_plan([first, second], "copy", True, cache, root / "logs")

            failure = raised.exception
            self.assertTrue(failure.partial_rollback)
            self.assertTrue(failure.log_path.is_file())
            self.assertEqual(failure.rollback_errors, ("rollback failed",))
            self.assertEqual(len(failure.applied_records), 1)
            self.assertEqual(failure.applied_records[0]["source"], str(first.source))

    def test_move_rejects_source_changed_since_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root)
            entry.source.write_bytes(b"changed-after-scan-and-longer")

            with self.assertRaises(ExecutionError):
                self._execute(root, entry, "move")

            self.assertTrue(entry.source.exists())
            self.assertFalse(entry.destination.exists())

    def test_failed_copy_cleanup_uses_the_created_file_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mkv"
            destination = root / "destination.mkv"
            source.write_bytes(b"media")

            def fail_after_partial_copy(source_handle, destination_handle, length):
                destination_handle.write(b"x")
                raise OSError("simulated copy failure")

            with mock.patch.object(
                executor,
                "_remove_created_destination",
                wraps=executor._remove_created_destination,
            ) as cleanup:
                with mock.patch.object(
                    executor.shutil,
                    "copyfileobj",
                    side_effect=fail_after_partial_copy,
                ):
                    with self.assertRaises(OSError):
                        executor._copy_exclusive(source, destination)

            cleanup.assert_called_once()
            self.assertIsNotNone(cleanup.call_args.args[1])
            self.assertFalse(destination.exists())

    def test_cross_volume_move_restores_source_and_discards_copy_if_staging_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root)
            original_copyfileobj = executor.shutil.copyfileobj
            original_link = executor.os.link

            def copy_then_change_source(source_handle, destination_handle, length):
                original_copyfileobj(source_handle, destination_handle, length)
                Path(source_handle.name).write_bytes(b"source-changed-during-copy")

            def fail_destination_link(source, destination, *args, **kwargs):
                if Path(destination) == entry.destination:
                    raise OSError(errno.EXDEV, "cross-device link")
                return original_link(source, destination, *args, **kwargs)

            with mock.patch.object(executor.os, "link", side_effect=fail_destination_link):
                with mock.patch.object(executor.shutil, "copyfileobj", side_effect=copy_then_change_source):
                    with self.assertRaises(ExecutionError):
                        self._execute(root, entry, "move")

            self.assertTrue(entry.source.exists())
            self.assertEqual(entry.source.read_bytes(), b"source-changed-during-copy")
            self.assertFalse(entry.destination.exists())

    def test_move_never_unlinks_a_file_recreated_at_the_original_source_path(self):
        for cross_volume in (False, True):
            with self.subTest(cross_volume=cross_volume):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    entry = self._entry(root)
                    original_unlink = Path.unlink
                    recreated_content = b"new-download-at-original-path"
                    injected = {"done": False}

                    def recreate_source_before_cleanup(path, *args, **kwargs):
                        if not injected["done"]:
                            preserved = entry.source.with_name(entry.source.name + ".preserved")
                            if entry.source.exists():
                                entry.source.rename(preserved)
                            entry.source.write_bytes(recreated_content)
                            injected["done"] = True
                        return original_unlink(path, *args, **kwargs)

                    link_patch = (
                        mock.patch.object(executor.os, "link", side_effect=OSError(errno.EXDEV, "cross-device link"))
                        if cross_volume
                        else mock.patch.object(executor.os, "link", wraps=executor.os.link)
                    )
                    with link_patch:
                        with mock.patch.object(Path, "unlink", new=recreate_source_before_cleanup):
                            self._execute(root, entry, "move")

                    self.assertTrue(injected["done"])
                    self.assertTrue(entry.source.exists())
                    self.assertEqual(entry.source.read_bytes(), recreated_content)
                    self.assertTrue(entry.destination.exists())

    def test_subtitle_move_uses_staging_and_preserves_recreated_source_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_entry = self._entry(root)
            subtitle = root / "incoming" / "episode.ass"
            subtitle.write_bytes(b"old-subtitle")
            subtitle_destination = root / "library" / "Test Show" / "episode.ass"
            entry = PlanEntry(
                subtitle,
                subtitle_destination,
                "organize",
                video_entry.resolution,
                "subtitle",
                str(video_entry.source),
            )
            original_unlink = Path.unlink
            recreated_content = b"new-subtitle-download"
            injected = {"done": False}

            def recreate_source_before_cleanup(path, *args, **kwargs):
                if not injected["done"]:
                    subtitle.write_bytes(recreated_content)
                    injected["done"] = True
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", new=recreate_source_before_cleanup):
                self._execute(root, entry, "move")

            self.assertTrue(injected["done"])
            self.assertEqual(subtitle.read_bytes(), recreated_content)
            self.assertEqual(subtitle_destination.read_bytes(), b"old-subtitle")

    def test_failed_move_preserves_staging_when_original_path_is_recreated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root)
            original_copyfileobj = executor.shutil.copyfileobj
            original_link = executor.os.link
            recreated_content = b"new-download-occupies-source"
            changed_staging_content = b"staging-changed-during-copy"

            def copy_then_change_staging(source_handle, destination_handle, length):
                original_copyfileobj(source_handle, destination_handle, length)
                Path(source_handle.name).write_bytes(changed_staging_content)
                entry.source.write_bytes(recreated_content)

            def fail_destination_link(source, destination, *args, **kwargs):
                if Path(destination) == entry.destination:
                    raise OSError(errno.EXDEV, "cross-device link")
                return original_link(source, destination, *args, **kwargs)

            with mock.patch.object(executor.os, "link", side_effect=fail_destination_link):
                with mock.patch.object(executor.shutil, "copyfileobj", side_effect=copy_then_change_staging):
                    with self.assertRaisesRegex(ExecutionError, "待恢复文件保留在.*partial"):
                        self._execute(root, entry, "move")

            staging_files = list(entry.source.parent.glob(".*.partial"))
            self.assertEqual(entry.source.read_bytes(), recreated_content)
            self.assertEqual(len(staging_files), 1)
            self.assertEqual(staging_files[0].read_bytes(), changed_staging_content)
            self.assertFalse(entry.destination.exists())

    def test_success_log_records_sha256_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root)

            log_path = self._execute(root, entry, "copy")

            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(
                record.get("result_sha256"),
                hashlib.sha256(entry.destination.read_bytes()).hexdigest(),
            )

    def test_copy_rollback_rejects_same_size_replacement_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root, b"original-content")
            log_path = self._execute(root, entry, "copy")
            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            replacement = b"tampered-content"
            self.assertEqual(len(replacement), len(b"original-content"))
            entry.destination.write_bytes(replacement)
            os.utime(
                str(entry.destination),
                ns=(int(record["result_mtime_ns"]), int(record["result_mtime_ns"])),
            )

            with self.assertRaisesRegex(ExecutionError, "摘要|变化"):
                rollback(log_path)

            self.assertTrue(entry.destination.exists())
            self.assertEqual(entry.destination.read_bytes(), replacement)

    def test_copy_rollback_refuses_legacy_log_without_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root)
            log_path = self._execute(root, entry, "copy")
            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            record.pop("result_sha256", None)
            log_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ExecutionError, "摘要"):
                rollback(log_path)

            self.assertTrue(entry.destination.exists())

    def test_rollback_syncs_database_after_resolution_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self._entry(root)
            with ResolutionCache(root / "library.sqlite3") as cache:
                cache.put(entry.resolution)
                log_path = execute_plan([entry], "copy", True, cache, root / "logs")
                revised = Resolution(
                    entry.resolution.media,
                    "Renamed Test Show",
                    1,
                    1,
                    False,
                    1.0,
                    True,
                    fingerprint="new-decision-fingerprint",
                )
                cache.put(revised)

                self.assertEqual(rollback(log_path, cache), 1)
                row = cache.connection.execute(
                    "SELECT current_path, status FROM media_files WHERE source_key IS NOT NULL"
                ).fetchone()

            self.assertEqual(row["current_path"], str(entry.source))
            self.assertEqual(row["status"], "identified")


if __name__ == "__main__":
    unittest.main()
