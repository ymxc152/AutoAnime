import tempfile
import unittest
from pathlib import Path


class RootsAndProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "Downloads"
        self.library = self.root / "Library"
        self.source.mkdir()
        self.library.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def services(self):
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService

        return RootService(self.database), ProfileService(self.database)

    def test_windows_paths_are_case_insensitive_and_duplicate_roots_are_rejected(self):
        from autoanime_v3.domain.errors import DuplicateRootError

        roots, unused_profiles = self.services()
        created = roots.create_root("source", self.source)

        self.assertEqual(created.normalized_path, str(self.source.resolve()).casefold())
        with self.assertRaises(DuplicateRootError):
            roots.create_root("source", Path(str(self.source).upper()))

    def test_library_equal_to_or_below_source_is_rejected(self):
        from autoanime_v3.domain.errors import UnsafeRootError

        roots, unused_profiles = self.services()
        roots.create_root("source", self.source)

        with self.assertRaises(UnsafeRootError):
            roots.create_root("library", self.source)
        nested = self.source / "organized"
        nested.mkdir()
        with self.assertRaises(UnsafeRootError):
            roots.create_root("library", nested)

    def test_operation_targets_cannot_escape_registered_root(self):
        from autoanime_v3.domain.errors import PathOutsideRootError

        roots, unused_profiles = self.services()
        library = roots.create_root("library", self.library)

        target = roots.resolve_target(library.id, Path("Show") / "Season 01" / "E01.mkv")
        self.assertTrue(str(target).casefold().startswith(str(self.library).casefold()))
        with self.assertRaises(PathOutsideRootError):
            roots.resolve_target(library.id, Path("..") / "escape.mkv")
        with self.assertRaises(PathOutsideRootError):
            roots.resolve_target(library.id, self.source / "absolute.mkv")

    def test_profile_updates_require_current_revision(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.domain.errors import RevisionConflictError

        roots, profiles = self.services()
        source = roots.create_root("source", self.source)
        library = roots.create_root("library", self.library)
        profile = profiles.create_profile(
            CreateProfile(
                name="新番自动整理",
                source_root_id=source.id,
                library_root_id=library.id,
                mode="link",
                execution_policy="review_all",
                min_confidence=85,
                stability_seconds=45,
                watch_enabled=True,
            )
        )

        changed = profiles.update_profile(
            profile.id,
            profile.revision,
            {"min_confidence": 90, "watch_enabled": False},
        )
        self.assertEqual(changed.revision, 2)
        self.assertEqual(changed.min_confidence, 90)
        self.assertFalse(changed.watch_enabled)
        with self.assertRaises(RevisionConflictError):
            profiles.update_profile(profile.id, profile.revision, {"min_confidence": 95})


if __name__ == "__main__":
    unittest.main()

