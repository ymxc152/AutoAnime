import os
import tempfile
import unittest
from pathlib import Path


class FileFactsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "source"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()

        from autoanime_v3.services.roots import RootService

        roots = RootService(self.database)
        self.source_root = roots.create_root("source", self.source)
        self.library_root = roots.create_root("library", self.library)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def repository(self):
        from autoanime_v3.db.repositories.library import LibraryRepository

        return LibraryRepository(self.database)

    def test_one_media_file_can_have_source_and_library_hardlink_locations(self):
        source_path = self.source / "[Group] Test Show - 01.mkv"
        source_path.write_bytes(b"real-media-fact" * 128)
        library_path = self.library / "Test Show" / "Season 01" / "E01.mkv"
        library_path.parent.mkdir(parents=True)
        os.link(str(source_path), str(library_path))

        repository = self.repository()
        source_media = repository.observe_path(
            self.source_root.id, source_path, "source", "video"
        )
        library_media = repository.observe_path(
            self.library_root.id, library_path, "library", "video"
        )

        self.assertEqual(source_media.id, library_media.id)
        refreshed = repository.get_media(source_media.id)
        self.assertEqual(
            {(location.role, location.state) for location in refreshed.locations},
            {("source", "present"), ("library", "present")},
        )

    def test_reused_path_creates_new_generation_and_replaces_old_location(self):
        path = self.source / "reused.mkv"
        path.write_bytes(b"generation-one")
        repository = self.repository()
        first = repository.observe_path(self.source_root.id, path, "source", "video")

        path.unlink()
        path.write_bytes(b"generation-two-is-different")
        second = repository.observe_path(self.source_root.id, path, "source", "video")

        self.assertNotEqual(first.id, second.id)
        old = repository.get_media(first.id)
        current = repository.get_media(second.id)
        self.assertEqual(old.locations[0].state, "replaced")
        self.assertEqual(current.locations[0].state, "present")


if __name__ == "__main__":
    unittest.main()
