import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from autoanime_v3.services.auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        from autoanime_v3.api.app import ServerSettings, create_app

        self.settings = ServerSettings(
            database_path=root / "web.sqlite3",
            data_directory=root,
            secure_cookies=False,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))

    def tearDown(self):
        self.client.close()
        self.temporary_directory.cleanup()

    def login_default(self):
        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def test_health_default_login_me_and_logout(self):
        self.assertEqual(self.client.get("/health/live").json()["status"], "live")
        self.assertEqual(self.client.get("/health/ready").json()["status"], "ready")
        status = self.client.get("/api/v1/auth/bootstrap-status").json()
        self.assertTrue(status["configured"])
        self.assertTrue(status["local_bypass"])
        self.assertTrue(status["can_local_login"])
        csrf = self.login_default()
        cookie = self.client.cookies.get("autoanime_session")
        self.assertTrue(cookie)
        me = self.client.get("/api/v1/auth/me")
        self.assertEqual(me.json()["username"], DEFAULT_ADMIN_USERNAME)
        logout = self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_local_session_on_loopback(self):
        response = self.client.post("/api/v1/auth/local-session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], DEFAULT_ADMIN_USERNAME)
        me = self.client.get("/api/v1/auth/me")
        self.assertEqual(me.status_code, 200)

    def test_remote_client_cannot_use_local_session(self):
        with TestClient(self.app, client=("203.0.113.10", 50000)) as remote:
            response = remote.post("/api/v1/auth/local-session")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "local_only")
            remote_status = remote.get("/api/v1/auth/bootstrap-status").json()
            self.assertTrue(remote_status["configured"])
            self.assertFalse(remote_status["local_client"])
            self.assertFalse(remote_status["can_local_login"])

    def test_local_bypass_can_be_disabled(self):
        csrf = self.login_default()
        settings = self.client.get("/api/v1/settings").json()
        revision = settings["security"]["local_bypass_revision"]
        disabled = self.client.patch(
            "/api/v1/settings",
            json={"key": "auth.local_bypass", "value": False, "revision": revision},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(disabled.status_code, 200)
        self.client.cookies.clear()
        blocked = self.client.post("/api/v1/auth/local-session")
        self.assertEqual(blocked.status_code, 401)
        status = self.client.get("/api/v1/auth/bootstrap-status").json()
        self.assertFalse(status["can_local_login"])

    def test_authenticated_root_creation_and_listing(self):
        csrf = self.login_default()
        source = Path(self.temporary_directory.name) / "source"
        source.mkdir()
        created = self.client.post(
            "/api/v1/roots",
            json={"kind": "source", "path": str(source)},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(created.status_code, 201)
        roots = self.client.get("/api/v1/roots")
        self.assertEqual(len(roots.json()["items"]), 1)

    def test_plan_item_decision_endpoints_require_csrf_and_validate_reason(self):
        csrf = self.login_default()
        root = Path(self.temporary_directory.name)
        source = root / "decision-source"
        library = root / "decision-library"
        source.mkdir()
        library.mkdir()
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        roots = RootService(self.settings.database_path)
        source_root = roots.create_root("source", source)
        library_root = roots.create_root("library", library)
        profile = ProfileService(self.settings.database_path).create_profile(
            CreateProfile("decision-profile", source_root.id, library_root.id)
        )
        (source / "测试番 S01E01.mkv").write_bytes(b"decision-media")
        outcome = ScanService(self.settings.database_path).run(profile.id)
        plan = self.client.get("/api/v1/plans/%s" % outcome.plan_id).json()
        item_id = plan["items"][0]["id"]
        approve_url = "/api/v1/plans/%s/items/%s/approve" % (outcome.plan_id, item_id)
        reject_url = "/api/v1/plans/%s/items/%s/reject" % (outcome.plan_id, item_id)

        self.assertEqual(self.client.post(approve_url).status_code, 403)
        approved = self.client.post(approve_url, headers={"X-CSRF-Token": csrf})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["items"][0]["decision"], "approved")

        empty = self.client.post(
            reject_url,
            json={"reason": "  "},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(empty.status_code, 422)
        rejected = self.client.post(
            reject_url,
            json={"reason": "not wanted"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["items"][0]["decision"], "rejected")
        self.assertEqual(rejected.json()["items"][0]["reject_reason"], "not wanted")

    def test_local_hook_requires_loopback_and_enabled_profile(self):
        csrf = self.login_default()
        source = Path(self.temporary_directory.name) / "hook-source"
        library = Path(self.temporary_directory.name) / "hook-library"
        source.mkdir()
        library.mkdir()
        source_id = self.client.post(
            "/api/v1/roots",
            json={"kind": "source", "path": str(source)},
            headers={"X-CSRF-Token": csrf},
        ).json()["id"]
        library_id = self.client.post(
            "/api/v1/roots",
            json={"kind": "library", "path": str(library)},
            headers={"X-CSRF-Token": csrf},
        ).json()["id"]
        self.client.post(
            "/api/v1/profiles",
            json={
                "name": "hook-profile",
                "source_root_id": source_id,
                "library_root_id": library_id,
            },
            headers={"X-CSRF-Token": csrf},
        )
        media = source / "show S01E01.mkv"
        media.write_bytes(b"x" * 1024)
        accepted = self.client.post(
            "/api/v1/hooks/local",
            json={"path": str(media)},
        )
        self.assertEqual(accepted.status_code, 202)
        with TestClient(self.app, client=("203.0.113.10", 50000)) as remote:
            rejected = remote.post("/api/v1/hooks/local", json={"path": str(media)})
            self.assertEqual(rejected.status_code, 403)
            self.assertEqual(rejected.json()["code"], "local_only")

    def test_remote_client_cannot_claim_first_administrator_when_empty(self):
        empty_root = Path(self.temporary_directory.name) / "empty"
        empty_root.mkdir()
        from autoanime_v3.api.app import ServerSettings, ServiceContainer, create_app

        settings = ServerSettings(
            database_path=empty_root / "web.sqlite3",
            data_directory=empty_root,
            secure_cookies=False,
            secret_provider="file",
        )
        services = ServiceContainer.build(settings)
        import sqlite3

        connection = sqlite3.connect(str(settings.database_path))
        connection.execute("DELETE FROM users")
        connection.commit()
        connection.close()
        app = create_app(settings, services=services)
        with TestClient(app, client=("203.0.113.10", 50000)) as remote:
            response = remote.post(
                "/api/v1/auth/bootstrap",
                json={"username": "attacker", "password": "Correct Horse Battery Staple!42"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "bootstrap_local_only")


    def test_pick_folder_requires_loopback(self):
        csrf = self.login_default()
        with TestClient(self.app, client=("203.0.113.10", 50000)) as remote:
            login = remote.post(
                "/api/v1/auth/login",
                json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
            )
            self.assertEqual(login.status_code, 200)
            remote_csrf = login.json()["csrf_token"]
            response = remote.post(
                "/api/v1/system/pick-folder",
                json={"title": "x"},
                headers={"X-CSRF-Token": remote_csrf},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "local_only")
        from autoanime_v3.api import app as app_module

        original = app_module.pick_folder_windows
        app_module.pick_folder_windows = lambda initial_directory=None, title="select": r"C:\Anime\Source"
        try:
            ok = self.client.post(
                "/api/v1/system/pick-folder",
                json={"title": "source"},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json()["path"], r"C:\Anime\Source")
            self.assertFalse(ok.json()["cancelled"])
        finally:
            app_module.pick_folder_windows = original

