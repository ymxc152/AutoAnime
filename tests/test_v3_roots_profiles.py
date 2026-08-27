import tempfile
import unittest
from pathlib import Path


class RootsAndProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def test_profile_roots_can_be_rebound_and_increment_revision(self):
        from autoanime_v3.domain.entities import CreateProfile

        roots, profiles = self.services()
        source = roots.create_root("source", self.source)
        library = roots.create_root("library", self.library)
        second_source_path = self.root / "Downloads-2"
        second_library_path = self.root / "Library-2"
        second_source_path.mkdir()
        second_library_path.mkdir()
        second_source = roots.create_root("source", second_source_path)
        second_library = roots.create_root("library", second_library_path)
        profile = profiles.create_profile(
            CreateProfile("profile", source.id, library.id)
        )

        rebound = profiles.update_profile(
            profile.id,
            profile.revision,
            {"source_root_id": second_source.id, "library_root_id": second_library.id},
        )

        self.assertEqual(rebound.source_root_id, second_source.id)
        self.assertEqual(rebound.library_root_id, second_library.id)
        self.assertGreater(rebound.revision, profile.revision)

    def test_profile_rebind_rejects_library_kind_for_source(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.domain.errors import ValidationError

        roots, profiles = self.services()
        source = roots.create_root("source", self.source)
        library = roots.create_root("library", self.library)
        profile = profiles.create_profile(CreateProfile("profile", source.id, library.id))

        with self.assertRaises(ValidationError):
            profiles.update_profile(
                profile.id, profile.revision, {"source_root_id": library.id}
            )

    def test_profile_rebind_rejects_source_kind_for_library(self):
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.domain.errors import ValidationError

        roots, profiles = self.services()
        source = roots.create_root("source", self.source)
        library = roots.create_root("library", self.library)
        profile = profiles.create_profile(CreateProfile("profile", source.id, library.id))

        with self.assertRaises(ValidationError):
            profiles.update_profile(
                profile.id, profile.revision, {"library_root_id": source.id}
            )

    def test_profile_rebind_rejects_nested_library_path(self):
        import sqlite3
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.domain.errors import UnsafeRootError
        from autoanime_v3.services.roots import normalize_windows_path

        roots, profiles = self.services()
        source = roots.create_root("source", self.source)
        library = roots.create_root("library", self.library)
        profile = profiles.create_profile(CreateProfile("profile", source.id, library.id))
        nested_library = self.source / "organized"
        nested_library.mkdir()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """
                INSERT INTO storage_roots(kind, path, normalized_path)
                VALUES (?, ?, ?)
                """,
                ("library", str(nested_library), normalize_windows_path(nested_library)),
            )
            nested_library_id = connection.execute(
                "SELECT id FROM storage_roots WHERE normalized_path = ?",
                (normalize_windows_path(nested_library),),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(UnsafeRootError):
            profiles.update_profile(
                profile.id, profile.revision, {"library_root_id": nested_library_id}
            )


if __name__ == "__main__":
    unittest.main()

