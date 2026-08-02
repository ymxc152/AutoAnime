import concurrent.futures
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoanime_v3.domain.errors import ValidationError


class ReviewAndPlanServiceTests(unittest.TestCase):
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
        self.profiles = ProfileService(self.database)
        self.profile = self.profiles.create_profile(
            CreateProfile(
                name="默认整理",
                source_root_id=self.source_root.id,
                library_root_id=self.library_root.id,
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def scan_safe(self):
        self.media = self.source / "测试番 S01E01.mkv"
        self.media.write_bytes(b"safe-media-content")
        from autoanime_v3.services.scans import ScanService

        return ScanService(self.database).run(self.profile.id)

    def scan_review(self, filename="Unknown Show - 02.mkv"):
        (self.source / filename).write_bytes(b"review-media")
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        ScanService(self.database).run(self.profile.id)
        return ReviewService(self.database).list_open()[0]

    def test_plan_item_decisions_are_persisted_and_audited(self):
        from autoanime_v3.db.engine import connect_sqlite
        from autoanime_v3.services.plans import PlanService

        outcome = self.scan_safe()
        service = PlanService(self.database)
        item_id = service.get(outcome.plan_id).items[0].id

        approved = service.decide_item(outcome.plan_id, item_id, "approved")
        self.assertEqual(approved.items[0].decision, "approved")
        self.assertIsNone(approved.items[0].reject_reason)

        rejected = service.decide_item(
            outcome.plan_id, item_id, "rejected", reason="wrong release"
        )
        self.assertEqual(rejected.items[0].decision, "rejected")
        self.assertEqual(rejected.items[0].reject_reason, "wrong release")
        self.assertTrue(rejected.items[0].decided_at)

        with self.assertRaises(ValidationError):
            service.decide_item(outcome.plan_id, item_id, "rejected", reason="  ")

        connection = connect_sqlite(self.database)
        try:
            events = connection.execute(
                "SELECT action, object_id, reason FROM audit_events ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(events[-2][0], "plan_item.approve")
        self.assertEqual(events[-1], ("plan_item.reject", str(item_id), "wrong release"))

    def test_rejected_item_does_not_block_plan_approval(self):
        from autoanime_v3.services.plans import PlanService

        outcome = self.scan_safe()
        service = PlanService(self.database)
        item = service.get(outcome.plan_id).items[0]
        service.decide_item(outcome.plan_id, item.id, "rejected", reason="not wanted")

        approved = service.approve(outcome.plan_id)

        self.assertEqual(approved.status, "approved")
        self.assertEqual(approved.items[0].decision, "rejected")

    def test_changed_source_identity_makes_plan_stale(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService

        outcome = self.scan_safe()
        self.media.write_bytes(b"changed-after-preview")
        with self.assertRaises(StalePlanError):
            PlanService(self.database).approve(outcome.plan_id)
        self.assertEqual(PlanService(self.database).get(outcome.plan_id).status, "stale")

    def test_changed_profile_revision_makes_plan_stale(self):
        from autoanime_v3.domain.errors import StalePlanError
        from autoanime_v3.services.plans import PlanService

        outcome = self.scan_safe()
        self.profiles.update_profile(self.profile.id, self.profile.revision, {"min_confidence": 92})
        with self.assertRaises(StalePlanError):
            PlanService(self.database).approve(outcome.plan_id)

    def test_destination_created_after_preview_prevents_approval(self):
        from autoanime_v3.domain.errors import PlanConflictError
        from autoanime_v3.services.plans import PlanService

        outcome = self.scan_safe()
        plan = PlanService(self.database).get(outcome.plan_id)
        destination = Path(plan.items[0].destination_path)
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"occupied")
        with self.assertRaises(PlanConflictError):
            PlanService(self.database).approve(outcome.plan_id)

    def test_resolving_review_creates_new_plan_revision_without_mutating_old(self):
        (self.source / "Unknown Show - 02.mkv").write_bytes(b"review-media")
        from autoanime_v3.db.engine import connect_sqlite
        from autoanime_v3.services.plans import PlanService
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        outcome = ScanService(self.database).run(self.profile.id)
        reviews = ReviewService(self.database).list_open()
        connection = connect_sqlite(self.database)
        try:
            source_location = connection.execute(
                """
                SELECT fl.*, mf.file_index, mf.size, mf.mtime_ns
                FROM file_locations fl
                JOIN media_files mf ON mf.id = fl.media_file_id
                WHERE fl.media_file_id = ? AND fl.role = 'source' AND fl.state = 'present'
                """,
                (reviews[0].media_file_id,),
            ).fetchone()
            source_location_id, source_file_index, source_size, source_mtime_ns = (
                source_location[0],
                source_location[-3],
                source_location[-2],
                source_location[-1],
            )
            connection.execute(
                """
                INSERT INTO plan_items(
                    plan_id, source_location_id, destination_root_id,
                    destination_relative_path, action, reason, risk_level,
                    source_file_index, source_size, source_mtime_ns,
                    identification_snapshot_json, execution_status
                ) VALUES (?, ?, ?, ?, 'link', 'legacy_review_item', 'normal', ?, ?, ?, '{}', 'pending')
                """,
                (
                    outcome.plan_id,
                    source_location_id,
                    self.library_root.id,
                    "legacy/Unknown Show - 02.mkv",
                    source_file_index,
                    source_size,
                    source_mtime_ns,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        old_plan_before = PlanService(self.database).get(outcome.plan_id)
        new_plan = ReviewService(self.database).resolve(
            reviews[0].id,
            {"title": "人工确认番剧", "season": 1, "episode": 2, "is_movie": False},
        )
        old_plan = PlanService(self.database).get(outcome.plan_id)

        self.assertEqual(old_plan.revision, 1)
        self.assertEqual(new_plan.revision, 2)
        self.assertNotEqual(old_plan.id, new_plan.id)
        self.assertEqual(old_plan, old_plan_before)
        self.assertEqual(
            sum(item.source_location_id == source_location_id for item in old_plan.items),
            1,
        )
        self.assertEqual(
            sum(item.source_location_id == source_location_id for item in new_plan.items),
            1,
        )
        self.assertEqual(ReviewService(self.database).get(reviews[0].id).status, "resolved")

    def test_resolving_review_persists_video_and_observed_subtitle_entries(self):
        video = self.source / "Unknown Show - 02.mkv"
        subtitle = self.source / "Unknown Show - 02.CHS.ass"
        video.write_bytes(b"review-video")
        subtitle.write_text("subtitle", encoding="utf-8")
        from autoanime_v3.db.repositories.library import LibraryRepository
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        ScanService(self.database).run(self.profile.id)
        subtitle_media = LibraryRepository(self.database).observe_path(
            self.source_root.id, subtitle, "source", "subtitle"
        )
        review = ReviewService(self.database).list_open()[0]

        new_plan = ReviewService(self.database).resolve(
            review.id,
            {"title": "人工确认番剧", "media_type": "episode", "season": 1, "episode": 2},
        )

        self.assertEqual({Path(item.source_path) for item in new_plan.items}, {video, subtitle})
        subtitle_item = next(item for item in new_plan.items if Path(item.source_path) == subtitle)
        self.assertEqual(subtitle_item.source_location_id, subtitle_media.locations[0].id)
        self.assertEqual(subtitle_item.reason, "subtitle")

    def test_resolving_review_reports_copied_destination_conflict_without_new_revision(self):
        review_source = self.source / "Unknown Show - 02.mkv"
        review_source.write_bytes(b"review-source")
        from autoanime_v3.db.repositories.library import LibraryRepository
        from autoanime_v3.domain.errors import PlanConflictError
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        outcome = ScanService(self.database).run(self.profile.id)
        service = ReviewService(self.database)
        review = service.list_open()[0]
        copied_source = self.source / "copied-plan-source.mkv"
        copied_source.write_bytes(b"copied-plan-source")
        copied_media = LibraryRepository(self.database).observe_path(
            self.source_root.id, copied_source, "source", "video"
        )
        copied_location = copied_media.locations[0]
        from autoanime_v3.models import MediaFile as CoreMediaFile, Resolution
        from autoanime_v3.planner import build_plan

        observed_review_source = Path(review.payload["source"])
        review_stat = observed_review_source.stat()
        accepted = Resolution(
            CoreMediaFile(
                path=observed_review_source,
                input_root=observed_review_source.parent,
                context_name=self.source.name,
                relative_path=review.payload["relative_path"],
                size=review_stat.st_size,
                mtime_ns=review_stat.st_mtime_ns,
            ),
            "测试番",
            1,
            1,
            False,
            1.0,
            True,
            media_type="episode",
        )
        conflicting_destination = str(
            build_plan([accepted], self.library)[0].destination.relative_to(self.library)
        )
        from autoanime_v3.db.engine import connect_sqlite

        connection = connect_sqlite(self.database)
        try:
            connection.execute(
                """
                INSERT INTO plan_items(
                    plan_id, source_location_id, destination_root_id,
                    destination_relative_path, action, reason, risk_level,
                    source_file_index, source_size, source_mtime_ns,
                    identification_snapshot_json, execution_status
                ) VALUES (?, ?, ?, ?, 'link', 'copied_item', 'normal', ?, ?, ?, '{}', 'pending')
                """,
                (
                    outcome.plan_id,
                    copied_location.id,
                    self.library_root.id,
                    conflicting_destination,
                    copied_media.file_index,
                    copied_media.size,
                    copied_media.mtime_ns,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(PlanConflictError) as raised:
            service.resolve(
                review.id,
                {"title": "测试番", "media_type": "episode", "season": 1, "episode": 1},
            )

        self.assertEqual(raised.exception.details["field"], "destination")
        self.assertEqual(service.get(review.id).status, "open")

        connection = connect_sqlite(self.database)
        try:
            revisions = connection.execute(
                "SELECT revision FROM plans WHERE scan_run_id = ? ORDER BY revision",
                (outcome.scan_run_id,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(revisions, [(1,)])

    def test_concurrent_resolve_atomically_claims_review_once(self):
        (self.source / "Unknown Show - 02.mkv").write_bytes(b"review-source")
        from autoanime_v3.db.engine import connect_sqlite
        from autoanime_v3.domain.errors import InvalidStateError
        from autoanime_v3.services import reviews as reviews_module
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.scans import ScanService

        outcome = ScanService(self.database).run(self.profile.id)
        review = ReviewService(self.database).list_open()[0]
        connection = connect_sqlite(self.database)
        try:
            first_user = connection.execute(
                "INSERT INTO users(username, password_hash) VALUES ('resolver-one', 'unused')"
            ).lastrowid
            second_user = connection.execute(
                "INSERT INTO users(username, password_hash) VALUES ('resolver-two', 'unused')"
            ).lastrowid
            connection.commit()
        finally:
            connection.close()

        barrier = threading.Barrier(2)
        original_normalize = reviews_module.normalize_resolution

        def synchronized_normalize(value):
            normalized = original_normalize(value)
            barrier.wait(timeout=5)
            return normalized

        def resolve(user_id):
            try:
                plan = ReviewService(self.database).resolve(
                    review.id,
                    {
                        "title": "人工确认番剧",
                        "media_type": "episode",
                        "season": 1,
                        "episode": 2,
                    },
                    user_id,
                )
                return ("resolved", user_id, plan.id)
            except Exception as error:
                return ("error", user_id, error)

        with patch.object(reviews_module, "normalize_resolution", side_effect=synchronized_normalize):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(resolve, (first_user, second_user)))

        resolved = [result for result in results if result[0] == "resolved"]
        errors = [result for result in results if result[0] == "error"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0][2], InvalidStateError)

        connection = connect_sqlite(self.database)
        try:
            stored = connection.execute(
                "SELECT status, resolved_by FROM review_items WHERE id = ?", (review.id,)
            ).fetchone()
            revisions = connection.execute(
                "SELECT revision FROM plans WHERE scan_run_id = ? ORDER BY revision",
                (outcome.scan_run_id,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(stored, ("resolved", resolved[0][1]))
        self.assertEqual(revisions, [(1,), (2,)])

    def test_resolving_episode_normalizes_s02e12_and_release_tag(self):
        from autoanime_v3.services.reviews import ReviewService

        review = self.scan_review("Unknown Show S02E12 WEB.mkv")
        new_plan = ReviewService(self.database).resolve(
            review.id,
            {
                "title": "人工确认番剧",
                "media_type": "episode",
                "season": "2",
                "episode": "12",
                "release_tag": "WEB-DL",
                "manual_lock": True,
            },
        )

        resolved = ReviewService(self.database).get(review.id)
        self.assertEqual(
            resolved.resolution,
            {
                "title": "人工确认番剧",
                "media_type": "episode",
                "season": 2,
                "episode": 12,
                "is_movie": False,
                "release_tag": "WEB-DL",
                "manual_lock": True,
            },
        )
        self.assertIn("Season 02", new_plan.items[-1].destination_path)
        self.assertIn("S02E12 - 人工确认番剧 [WEB-DL]", new_plan.items[-1].destination_path)

    def test_resolving_movie_omits_episode_fields_and_builds_movie_plan(self):
        from autoanime_v3.services.reviews import ReviewService

        review = self.scan_review("Unknown Movie.mkv")
        new_plan = ReviewService(self.database).resolve(
            review.id,
            {
                "title": "人工确认电影",
                "media_type": "movie",
                "release_tag": "BDRip",
                "manual_lock": True,
            },
        )

        resolution = ReviewService(self.database).get(review.id).resolution
        self.assertNotIn("season", resolution)
        self.assertNotIn("episode", resolution)
        self.assertTrue(resolution["is_movie"])
        self.assertEqual(resolution["media_type"], "movie")
        self.assertIn("人工确认电影 [BDRip].mkv", new_plan.items[-1].destination_path)

    def test_resolving_special_preserves_sp_episode_and_builds_executable_plan(self):
        from autoanime_v3.services.reviews import ReviewService

        review = self.scan_review("Unknown Show SP03.mkv")
        new_plan = ReviewService(self.database).resolve(
            review.id,
            {
                "title": "人工确认番剧",
                "media_type": "special",
                "season": 0,
                "episode": "SP03",
                "release_tag": "",
                "manual_lock": True,
            },
        )

        resolved = ReviewService(self.database).get(review.id)
        self.assertEqual(resolved.resolution["season"], 0)
        self.assertEqual(resolved.resolution["episode"], "SP03")
        self.assertEqual(new_plan.items[-1].action, "link")
        self.assertIn("Specials", new_plan.items[-1].destination_path)
        self.assertIn("SP03 - 人工确认番剧", new_plan.items[-1].destination_path)
        self.assertTrue(new_plan.items[-1].destination_path.endswith(".mkv"))

    def test_resolving_decimal_episode_does_not_truncate_value(self):
        from autoanime_v3.services.reviews import ReviewService

        review = self.scan_review("Unknown Show - 12.5.mkv")
        new_plan = ReviewService(self.database).resolve(
            review.id,
            {"title": "分段番剧", "media_type": "episode", "season": 1, "episode": "12.5"},
        )

        self.assertEqual(ReviewService(self.database).get(review.id).resolution["episode"], 12.5)
        self.assertIn("S01E12.5 - 分段番剧", new_plan.items[-1].destination_path)

    def test_invalid_structured_resolutions_are_rejected_without_resolving_review(self):
        from autoanime_v3.services.reviews import ReviewService

        review = self.scan_review()
        service = ReviewService(self.database)
        invalid_values = (
            ({"media_type": "episode", "season": 1, "episode": 2}, "title"),
            ({"title": "番剧", "media_type": "episode", "episode": 2}, "season"),
            ({"title": "番剧", "media_type": "episode", "season": 1}, "episode"),
            ({"title": "电影", "media_type": "movie", "season": 1}, "season"),
            ({"title": "番剧", "media_type": "ova", "season": 1, "episode": 1}, "media_type"),
            ({"title": "番剧", "media_type": [], "season": 1, "episode": 1}, "media_type"),
            ({"title": "番剧", "media_type": "episode", "season": -1, "episode": 1}, "season"),
            ({"title": "番剧", "media_type": "episode", "season": 1, "episode": True}, "episode"),
            ({"title": "番剧", "media_type": "special", "episode": "../SP01"}, "episode"),
        )

        for resolution, field in invalid_values:
            with self.subTest(resolution=resolution):
                with self.assertRaises(ValidationError) as raised:
                    service.resolve(review.id, resolution)
                self.assertEqual(raised.exception.details["field"], field)
                self.assertEqual(service.get(review.id).status, "open")

    def test_unknown_resolution_fields_are_rejected_with_stable_field(self):
        from autoanime_v3.services.reviews import normalize_resolution

        for field in ("unexpected", "manual_lcok"):
            with self.subTest(field=field):
                resolution = {
                    "title": "番剧",
                    "media_type": "episode",
                    "season": 1,
                    "episode": 1,
                    field: True,
                }
                with self.assertRaises(ValidationError) as raised:
                    normalize_resolution(resolution)
                self.assertEqual(raised.exception.details["field"], field)


if __name__ == "__main__":
    unittest.main()
