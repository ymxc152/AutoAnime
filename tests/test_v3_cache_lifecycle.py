import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from autoanime_v3.cache import ResolutionCache, fingerprint
from autoanime_v3.models import MediaFile, Resolution


class ResolutionCacheLifecycleTests(unittest.TestCase):
    def _media(self, root: Path) -> MediaFile:
        path = root / "Example.S01E01.mkv"
        path.write_bytes(b"video")
        stat = path.stat()
        return MediaFile(path, root, "bundle", path.name, stat.st_size, stat.st_mtime_ns)

    def _resolution(self, media: MediaFile, title: str, decision_version: str) -> Resolution:
        return Resolution(
            media=media,
            canonical_title=title,
            season=1,
            episode=1,
            confidence=0.99,
            accepted=True,
            fingerprint=fingerprint(media, decision_version),
        )

    def test_rule_change_replaces_current_fact_for_same_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = self._media(root)
            old_resolution = self._resolution(media, "\u65e7\u6807\u9898", "rules-v1")
            new_resolution = self._resolution(media, "\u65b0\u6807\u9898", "rules-v2")

            with ResolutionCache(root / "library.sqlite3") as cache:
                cache.put(old_resolution)
                cache.put(new_resolution)

                rows = cache.connection.execute(
                    "SELECT fingerprint, episode_id FROM media_files"
                ).fetchall()
                self.assertEqual(1, len(rows))
                self.assertEqual(new_resolution.fingerprint, rows[0]["fingerprint"])

                progress = cache.list_show_progress()
                self.assertEqual(["\u65b0\u6807\u9898"], [row["canonical_title"] for row in progress])
                old_show_id = cache.connection.execute(
                    "SELECT id FROM shows WHERE canonical_title='\u65e7\u6807\u9898'"
                ).fetchone()[0]
                self.assertEqual([], cache.show_detail(old_show_id)["episodes"])

                cached = cache.get(media, "rules-v2")
                self.assertIsNotNone(cached)
                self.assertEqual("\u65b0\u6807\u9898", cached.canonical_title)

    def test_reused_download_path_does_not_inherit_old_organized_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = self._media(root)
            old_resolution = self._resolution(media, "\u65e7\u6807\u9898", "rules-v1")
            organized_path = root / "library" / "\u65e7\u6807\u9898" / "S01E01.mkv"

            with ResolutionCache(root / "library.sqlite3") as cache:
                cache.put(old_resolution)
                cache.mark_organized(old_resolution, organized_path)

                media.path.unlink()
                media.path.write_bytes(b"a completely new download")
                new_stat = media.path.stat()
                replacement_media = MediaFile(
                    media.path,
                    root,
                    "bundle",
                    media.path.name,
                    new_stat.st_size,
                    new_stat.st_mtime_ns,
                )
                replacement = self._resolution(replacement_media, "\u65b0\u6807\u9898", "rules-v2")
                cache.put(replacement)

                row = cache.connection.execute(
                    "SELECT current_path, status FROM media_files"
                ).fetchone()

            self.assertEqual(str(media.path), row["current_path"])
            self.assertEqual("identified", row["status"])

    def test_v1_database_is_migrated_without_losing_audit_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "library.sqlite3"
            source = str(Path(directory) / "Example.S01E01.mkv")
            organized_destination = str(Path(directory) / "library" / "S01E01.mkv")
            with closing(sqlite3.connect(str(database))) as connection:
                connection.executescript(
                    """
                    CREATE TABLE media_files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fingerprint TEXT NOT NULL UNIQUE,
                        episode_id INTEGER,
                        original_path TEXT NOT NULL,
                        current_path TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        release_tag TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'identified',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE operations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        source TEXT NOT NULL,
                        destination TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE corrections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_type TEXT NOT NULL,
                        entity_id INTEGER NOT NULL,
                        field_name TEXT NOT NULL,
                        old_value TEXT NOT NULL,
                        new_value TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'draft',
                        migration_plan_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        applied_at TEXT
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO media_files(fingerprint, original_path, current_path, size, mtime_ns, status) "
                    "VALUES('old-fingerprint', ?, ?, 5, 1, 'organized')",
                    (source, organized_destination),
                )
                connection.execute(
                    "INSERT INTO media_files(fingerprint, original_path, current_path, size, mtime_ns) "
                    "VALUES('new-fingerprint', ?, ?, 5, 1)",
                    (source, source),
                )
                connection.execute(
                    "INSERT INTO operations(run_id, action, source, destination, status) "
                    "VALUES('run-1', 'move', ?, 'destination', 'done')",
                    (source,),
                )
                connection.execute(
                    "INSERT INTO corrections(entity_type, entity_id, field_name, old_value, new_value) "
                    "VALUES('show', 1, 'canonical_title', '\u65e7\u6807\u9898', '\u65b0\u6807\u9898')"
                )
                connection.commit()

            with ResolutionCache(database) as cache:
                columns = {
                    row["name"] for row in cache.connection.execute("PRAGMA table_info(media_files)")
                }
                self.assertIn("source_key", columns)
                rows = cache.connection.execute(
                    "SELECT fingerprint, source_key, current_path, status FROM media_files"
                ).fetchall()
                self.assertEqual(1, len(rows))
                self.assertEqual("new-fingerprint", rows[0]["fingerprint"])
                self.assertTrue(rows[0]["source_key"])
                self.assertEqual(organized_destination, rows[0]["current_path"])
                self.assertEqual("organized", rows[0]["status"])
                self.assertEqual(
                    "2",
                    cache.connection.execute(
                        "SELECT value FROM meta WHERE key='schema_version'"
                    ).fetchone()[0],
                )
                self.assertEqual(1, cache.connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
                self.assertEqual(1, cache.connection.execute("SELECT COUNT(*) FROM corrections").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
