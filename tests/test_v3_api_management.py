import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class ApiManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        from autoanime_v3.api.app import ServerSettings, create_app

        self.settings = ServerSettings(
            database_path=self.root / "web.sqlite3",
            data_directory=self.root,
            secure_cookies=False,
        )
        self.client = TestClient(
            create_app(self.settings), client=("127.0.0.1", 50000)
        )

    def tearDown(self):
        self.client.close()
        self.temporary_directory.cleanup()

    def login(self):
        from autoanime_v3.services.auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME

        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return {"X-CSRF-Token": response.json()["csrf_token"]}

    def test_bootstrap_status_distinguishes_first_run_from_logged_out(self):
        status = self.client.get("/api/v1/auth/bootstrap-status").json()
        self.assertTrue(status["configured"])
        self.assertTrue(status["local_bypass"])
        self.assertTrue(status["local_client"])
        self.assertTrue(status["can_local_login"])
        self.login()
        status = self.client.get("/api/v1/auth/bootstrap-status").json()
        self.assertTrue(status["configured"])

    def test_settings_update_uses_revisions_and_returns_json_values(self):
        headers = self.login()
        created = self.client.patch(
            "/api/v1/settings",
            json={"key": "backup.retention_days", "value": 14, "revision": 0},
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["value"], 14)
        self.assertEqual(created.json()["revision"], 1)

        conflict = self.client.patch(
            "/api/v1/settings",
            json={"key": "backup.retention_days", "value": 30, "revision": 0},
            headers=headers,
        )
        self.assertEqual(conflict.status_code, 409)
        listed = {
            item["key"]: item["value"]
            for item in self.client.get("/api/v1/settings").json()["items"]
        }
        self.assertEqual(listed["backup.retention_days"], 14)
        self.assertIn("auth.local_bypass", listed)
        self.assertTrue(listed["auth.local_bypass"])

    def test_schedule_and_webhook_management_and_anonymous_downloader_hook(self):
        headers = self.login()
        source = self.root / "automation-source"
        library = self.root / "automation-library"
        source.mkdir()
        library.mkdir()
        source_id = self.client.post(
            "/api/v1/roots", json={"kind": "source", "path": str(source)}, headers=headers
        ).json()["id"]
        library_id = self.client.post(
            "/api/v1/roots", json={"kind": "library", "path": str(library)}, headers=headers
        ).json()["id"]
        profile = self.client.post(
            "/api/v1/profiles",
            json={"name": "自动化", "source_root_id": source_id, "library_root_id": library_id},
            headers=headers,
        ).json()

        missing_csrf = self.client.post(
            "/api/v1/schedules",
            json={"profile_id": profile["id"], "kind": "interval", "schedule": {"interval_minutes": 5}, "timezone": "UTC"},
        )
        self.assertEqual(missing_csrf.status_code, 403)
        schedule = self.client.post(
            "/api/v1/schedules",
            json={"profile_id": profile["id"], "kind": "interval", "schedule": {"interval_minutes": 5}, "timezone": "UTC"},
            headers=headers,
        )
        self.assertEqual(schedule.status_code, 201)
        self.assertEqual(self.client.get("/api/v1/schedules").json()["items"][0]["revision"], 1)

        created = self.client.post(
            "/api/v1/webhook-sources",
            json={"name": "qBittorrent", "downloader": "qbittorrent", "profile_id": profile["id"]},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        token = created.json()["token"]
        listing = self.client.get("/api/v1/webhook-sources").json()["items"][0]
        self.assertNotIn("token", listing)
        self.assertNotIn("token_hash", listing)

        target = source / "completed.mkv"
        target.write_bytes(b"complete")
        accepted = self.client.post(
            "/api/v1/hooks/downloaders/%s" % token,
            json={"path": str(target)},
        )
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.json()["payload"]["paths"], [str(target.resolve())])

        aliased = self.client.post(
            "/api/v1/hooks/downloaders/%s" % token,
            json={"savePath": str(target), "foo": 1},
        )
        self.assertEqual(aliased.status_code, 202)

        memory = self.client.get("/api/v1/memory")
        self.assertEqual(memory.status_code, 200)
        self.assertIsInstance(memory.json()["items"], list)

        disabled = self.client.patch(
            "/api/v1/webhook-sources/%s" % created.json()["id"],
            json={"revision": created.json()["revision"], "patch": {"enabled": False}},
            headers=headers,
        )
        self.assertEqual(disabled.status_code, 200)
        rejected = self.client.post(
            "/api/v1/hooks/downloaders/%s" % token,
            json={"paths": [str(target)]},
        )
        self.assertEqual(rejected.status_code, 404)

        import sqlite3
        connection = sqlite3.connect(str(self.settings.database_path))
        try:
            connection.execute("UPDATE jobs SET status = 'succeeded' WHERE job_type = 'scan'")
            connection.commit()
        finally:
            connection.close()

        deleted = self.client.request(
            "DELETE",
            "/api/v1/profiles/%s" % profile["id"],
            json={"revision": profile["revision"]},
            headers=headers,
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/schedules").json()["items"], [])
        self.assertEqual(self.client.get("/api/v1/webhook-sources").json()["items"], [])

    def test_storage_root_can_be_disabled_and_revalidated(self):
        headers = self.login()
        source = self.root / "source"
        source.mkdir()
        created = self.client.post(
            "/api/v1/roots",
            json={"kind": "source", "path": str(source)},
            headers=headers,
        ).json()
        updated = self.client.patch(
            "/api/v1/roots/%s" % created["id"],
            json={"patch": {"enabled": False}},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["enabled"])
        health = self.client.post(
            "/api/v1/roots/%s/validate" % created["id"], headers=headers
        )
        self.assertEqual(health.json()["health_status"], "healthy")

    def test_reenabling_root_rechecks_source_library_overlap(self):
        headers = self.login()
        source = self.root / "overlap-source"
        library = source / "library"
        library.mkdir(parents=True)
        source_root = self.client.post(
            "/api/v1/roots", json={"kind": "source", "path": str(source)}, headers=headers
        ).json()
        self.client.patch(
            "/api/v1/roots/%s" % source_root["id"],
            json={"patch": {"enabled": False}},
            headers=headers,
        )
        created_library = self.client.post(
            "/api/v1/roots", json={"kind": "library", "path": str(library)}, headers=headers
        )
        self.assertEqual(created_library.status_code, 201)
        unsafe = self.client.patch(
            "/api/v1/roots/%s" % source_root["id"],
            json={"patch": {"enabled": True}},
            headers=headers,
        )
        self.assertEqual(unsafe.status_code, 409)
        self.assertEqual(unsafe.json()["code"], "unsafe_root")
        wrong_type = self.client.patch(
            "/api/v1/roots/%s" % source_root["id"],
            json={"patch": {"enabled": "false"}},
            headers=headers,
        )
        self.assertEqual(wrong_type.status_code, 422)

    def test_profile_patch_rejects_invalid_modes_instead_of_persisting_them(self):
        headers = self.login()
        source = self.root / "profile-source"
        library = self.root / "profile-library"
        source.mkdir()
        library.mkdir()
        source_id = self.client.post(
            "/api/v1/roots", json={"kind": "source", "path": str(source)}, headers=headers
        ).json()["id"]
        library_id = self.client.post(
            "/api/v1/roots", json={"kind": "library", "path": str(library)}, headers=headers
        ).json()["id"]
        profile = self.client.post(
            "/api/v1/profiles",
            json={"name": "默认", "source_root_id": source_id, "library_root_id": library_id},
            headers=headers,
        ).json()
        invalid = self.client.patch(
            "/api/v1/profiles/%s" % profile["id"],
            json={"revision": profile["revision"], "patch": {"mode": "overwrite"}},
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 422)
        invalid_number = self.client.patch(
            "/api/v1/profiles/%s" % profile["id"],
            json={"revision": profile["revision"], "patch": {"min_confidence": "high"}},
            headers=headers,
        )
        self.assertEqual(invalid_number.status_code, 422)
        persisted = self.client.get("/api/v1/profiles").json()["items"][0]
        self.assertEqual(persisted["mode"], "link")

    def test_rule_revision_lifecycle_is_available_through_api(self):
        headers = self.login()
        rule_set = self.client.post(
            "/api/v1/rules",
            json={"name": "默认别名"},
            headers=headers,
        )
        self.assertEqual(rule_set.status_code, 201)
        revision = self.client.post(
            "/api/v1/rules/revisions",
            json={
                "rule_set_id": rule_set.json()["id"],
                "document": {"aliases": {"Frieren": "葬送的芙莉莲"}},
            },
            headers=headers,
        )
        self.assertEqual(revision.status_code, 201)
        validated = self.client.post(
            "/api/v1/rules/revisions/%s/validate" % revision.json()["id"],
            headers=headers,
        )
        self.assertEqual(validated.json()["status"], "validated")
        active = self.client.post(
            "/api/v1/rules/revisions/%s/activate" % revision.json()["id"],
            headers=headers,
        )
        self.assertEqual(active.json()["status"], "active")
        listing = self.client.get("/api/v1/rules").json()["items"]
        self.assertEqual(listing[0]["revisions"][0]["document"]["aliases"]["Frieren"], "葬送的芙莉莲")

    def test_show_correction_can_be_previewed_and_applied_through_api(self):
        headers = self.login()
        from autoanime_v3.services.changes import ChangeService

        show = ChangeService(self.settings.database_path).create_show("旧标题")
        preview = self.client.post(
            "/api/v1/library/changes/preview",
            json={
                "show_id": show.id,
                "base_revision": show.revision,
                "patch": {"canonical_title": "新标题", "title_locked": True},
                "reason": "人工纠正",
            },
            headers=headers,
        )
        self.assertEqual(preview.status_code, 201)
        applied = self.client.post(
            "/api/v1/library/changes/%s/approve" % preview.json()["id"],
            headers=headers,
        )
        self.assertEqual(applied.json()["canonical_title"], "新标题")
        self.assertTrue(applied.json()["title_locked"])

    def test_static_frontend_falls_back_to_index_for_client_routes(self):
        self.client.close()
        frontend = self.root / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("<html><title>AutoAnime</title></html>", encoding="utf-8")
        from autoanime_v3.api.app import ServerSettings, create_app

        settings = ServerSettings(
            database_path=self.root / "spa.sqlite3",
            data_directory=self.root / "spa-data",
            secure_cookies=False,
            frontend_directory=frontend,
        )
        self.client = TestClient(
            create_app(settings), client=("127.0.0.1", 50000)
        )
        response = self.client.get("/profiles")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AutoAnime", response.text)

    def test_review_resolution_validation_has_stable_422_envelope(self):
        headers = self.login()
        source = self.root / "review-source"
        library = self.root / "review-library"
        source.mkdir()
        library.mkdir()
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.reviews import ReviewService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        roots = RootService(self.settings.database_path)
        source_root = roots.create_root("source", source)
        library_root = roots.create_root("library", library)
        profile = ProfileService(self.settings.database_path).create_profile(
            CreateProfile(
                name="审核验证",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
            )
        )
        (source / "Unknown Show - 02.mkv").write_bytes(b"review-media")
        ScanService(self.settings.database_path).run(profile.id)
        review = ReviewService(self.settings.database_path).list_open()[0]

        response = self.client.post(
            "/api/v1/reviews/%s/resolve" % review.id,
            json={"resolution": {"title": "番剧", "media_type": "episode", "episode": 2}},
            headers=headers,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(response.json()["details"]["field"], "season")

        for invalid_resolution in ([], "not-an-object", None):
            with self.subTest(resolution=invalid_resolution):
                response = self.client.post(
                    "/api/v1/reviews/%s/resolve" % review.id,
                    json={"resolution": invalid_resolution},
                    headers={**headers, "X-Trace-ID": "review-resolution-validation"},
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json(),
                    {
                        "code": "validation_error",
                        "message": "Resolution must be an object",
                        "details": {"field": "resolution"},
                        "trace_id": "review-resolution-validation",
                    },
                )


if __name__ == "__main__":
    unittest.main()
