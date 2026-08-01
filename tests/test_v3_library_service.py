import tempfile
import unittest
from pathlib import Path

from autoanime_v3.cache import ResolutionCache
from autoanime_v3.library_service import LibraryService
from autoanime_v3.models import MediaFile, Resolution


class LibraryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def media(self, name, content=b"video"):
        source = self.root / name
        source.write_bytes(content)
        stat = source.stat()
        return MediaFile(source, self.root, "bundle", source.name, stat.st_size, stat.st_mtime_ns)

    def test_library_progress_and_title_correction_preview(self):
        media = self.media("Show.S01E01.mkv")
        resolution = Resolution(media, "旧番名", 1, 1, False, 0.99, True, "Baha", fingerprint="fixture")
        with ResolutionCache(self.root / "library.sqlite3") as repository:
            repository.put(resolution)
            repository.mark_organized(resolution, self.root / "library" / "旧番名" / "Season 01" / "S01E01 - 旧番名.mkv")
            service = LibraryService(repository, self.root / "library")
            shows = service.list_shows()
            self.assertEqual(shows[0]["organized_episodes"], 1)
            preview = service.preview_show_title_change(shows[0]["show_id"], "新番名", "测试纠正")
            self.assertEqual(preview["status"], "draft")
            self.assertIn("S01E01 - 新番名.mkv", preview["moves"][0]["destination"])
            self.assertEqual(preview["conflicts"], 0)

    def test_title_correction_preserves_version_suffix_and_ignores_unorganized_files(self):
        organized_media = self.media("organized-source.mkv")
        organized = Resolution(organized_media, "旧番名", 1, 1, False, 0.99, True, "Baha", fingerprint="organized")
        pending_media = self.media("pending.mkv")
        pending = Resolution(pending_media, "旧番名", 1, 2, False, 0.99, True, "", fingerprint="pending")

        with ResolutionCache(self.root / "library.sqlite3") as repository:
            repository.put(organized)
            repository.put(pending)
            current = self.root / "library" / "旧番名" / "Season 01" / "S01E01 - 旧番名 [Baha-abcd1234].mkv"
            repository.mark_organized(organized, current)
            service = LibraryService(repository, self.root / "library")
            show_id = service.list_shows()[0]["show_id"]
            preview = service.preview_show_title_change(show_id, "新番名")
            self.assertEqual(len(preview["moves"]), 1)
            self.assertTrue(preview["moves"][0]["destination"].endswith("S01E01 - 新番名 [Baha-abcd1234].mkv"))


if __name__ == "__main__":
    unittest.main()
