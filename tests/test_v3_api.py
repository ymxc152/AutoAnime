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

    def test_delete_root_blocks_in_use_and_deletes_free_root(self):
        csrf = self.login_default()
        base = Path(self.temporary_directory.name)
        source = base / "del-source"
        library = base / "del-library"
        spare = base / "del-spare"
        for path in (source, library, spare):
            path.mkdir()
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService

        roots = RootService(self.settings.database_path)
        source_root = roots.create_root("source", source)
        library_root = roots.create_root("library", library)
        spare_root = roots.create_root("operations", spare)
        ProfileService(self.settings.database_path).create_profile(
            CreateProfile("del-profile", source_root.id, library_root.id)
        )
        headers = {"X-CSRF-Token": csrf}
        listed_roots = {
            item["id"]: item for item in self.client.get("/api/v1/roots").json()["items"]
        }
        self.assertEqual(listed_roots[library_root.id]["profile_count"], 1)
        self.assertEqual(listed_roots[library_root.id]["file_count"], 0)
        # A root referenced by a profile cannot be deleted.
        blocked = self.client.delete(
            "/api/v1/roots/%s" % library_root.id, headers=headers
        )
        self.assertEqual(blocked.status_code, 422)
        self.assertEqual(
            blocked.json()["message"],
            "Storage root is used by a scan profile and cannot be deleted; disable the root instead",
        )
        # A free root (operations log) can be deleted.
        deleted = self.client.delete("/api/v1/roots/%s" % spare_root.id, headers=headers)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(len(self.client.get("/api/v1/roots").json()["items"]), 2)
        # Mutating requires CSRF.
        self.assertEqual(
            self.client.delete("/api/v1/roots/%s" % spare_root.id).status_code, 403
        )
        # A logically deleted profile still protects its historical root reference.
        self.assertEqual(
            self.client.request(
                "DELETE",
                "/api/v1/profiles/1",
                json={"revision": 1},
                headers=headers,
            ).status_code,
            204,
        )
        self.assertEqual(
            self.client.delete("/api/v1/roots/%s" % library_root.id, headers=headers).status_code,
            422,
        )

    def test_delete_profile_preserves_history_and_hides_profile(self):
        csrf = self.login_default()
        base = Path(self.temporary_directory.name)
        source = base / "p-source"
        library = base / "p-library"
        source.mkdir()
        library.mkdir()
        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        roots = RootService(self.settings.database_path)
        source_root = roots.create_root("source", source)
        library_root = roots.create_root("library", library)
        profiles = ProfileService(self.settings.database_path)
        used = profiles.create_profile(
            CreateProfile("used-profile", source_root.id, library_root.id)
        )
        (source / "删除测试 S01E01.mkv").write_bytes(b"x" * 512)
        outcome = ScanService(self.settings.database_path).run(used.id)
        headers = {"X-CSRF-Token": csrf}
        # A profile with scan/plan history is logically deleted.
        deleted_used = self.client.request(
            "DELETE",
            "/api/v1/profiles/%s" % used.id,
            json={"revision": used.revision},
            headers=headers,
        )
        self.assertEqual(deleted_used.status_code, 204)
        blocked_update = self.client.patch(
            "/api/v1/profiles/%s" % used.id,
            json={"revision": used.revision, "patch": {"enabled": True}},
            headers=headers,
        )
        self.assertEqual(blocked_update.status_code, 422)
        blocked_scan = self.client.post(
            "/api/v1/jobs/scans",
            json={"profile_id": used.id, "paths": []},
            headers=headers,
        )
        self.assertEqual(blocked_scan.status_code, 422)
        self.assertEqual(
            blocked_scan.json()["message"],
            "Scan profile has been deleted and cannot start new scans",
        )

        import json
        import sqlite3
        connection = sqlite3.connect(str(self.settings.database_path))
        connection.row_factory = sqlite3.Row
        try:
            profile = connection.execute(
                "SELECT deleted_at, deleted_snapshot_json FROM scan_profiles WHERE id = ?",
                (used.id,),
            ).fetchone()
            run = connection.execute(
                "SELECT profile_snapshot_json FROM scan_runs WHERE profile_id = ?",
                (used.id,),
            ).fetchone()
            plan = connection.execute(
                "SELECT profile_snapshot_json FROM plans WHERE profile_id = ?",
                (used.id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(profile["deleted_at"])
        self.assertEqual(json.loads(profile["deleted_snapshot_json"])["name"], "used-profile")
        self.assertEqual(json.loads(run["profile_snapshot_json"])["name"], "used-profile")
        self.assertEqual(json.loads(plan["profile_snapshot_json"])["name"], "used-profile")
        history_plans = self.client.get("/api/v1/plans").json()["items"]
        history_plan = next(item for item in history_plans if item["id"] == outcome.plan_id)
        self.assertEqual(history_plan["profile_name"], "used-profile")
        self.assertEqual(history_plan["profile_snapshot"]["source_path"], str(source.resolve()))
        # A fresh profile without history can be deleted.
        fresh = profiles.create_profile(
            CreateProfile("fresh-profile", source_root.id, library_root.id)
        )
        listed = {
            item["id"]: item
            for item in self.client.get("/api/v1/profiles").json()["items"]
        }
        self.assertNotIn(used.id, listed)
        self.assertEqual(listed[fresh.id]["scan_runs"], 0)
        self.assertEqual(listed[fresh.id]["plans"], 0)
        deleted = self.client.request(
            "DELETE",
            "/api/v1/profiles/%s" % fresh.id,
            json={"revision": fresh.revision},
            headers=headers,
        )
        self.assertEqual(deleted.status_code, 204)
        remaining = self.client.get("/api/v1/profiles").json()["items"]
        self.assertEqual(remaining, [])
        # Stale revision is rejected.
        stale = self.client.request(
            "DELETE",
            "/api/v1/profiles/%s" % used.id,
            json={"revision": 0},
            headers=headers,
        )
        self.assertEqual(stale.status_code, 404)

    def test_delete_plan_dismisses_stale_and_guards_open_reviews(self):
        csrf = self.login_default()
        base = Path(self.temporary_directory.name)
        source = base / "plan-source"
        library = base / "plan-library"
        source.mkdir()
        library.mkdir()
        import sqlite3

        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        roots = RootService(self.settings.database_path)
        source_root = roots.create_root("source", source)
        library_root = roots.create_root("library", library)
        profile = ProfileService(self.settings.database_path).create_profile(
            CreateProfile("plan-profile", source_root.id, library_root.id)
        )
        (source / "计划测试 S01E01.mkv").write_bytes(b"x" * 512)
        outcome = ScanService(self.settings.database_path).run(profile.id)
        plan_id = outcome.plan_id
        headers = {"X-CSRF-Token": csrf}
        # An active plan cannot be dismissed.
        active = self.client.delete("/api/v1/plans/%s" % plan_id, headers=headers)
        self.assertEqual(active.status_code, 422)
        # Once stale, dismissal succeeds.
        connection = sqlite3.connect(str(self.settings.database_path))
        connection.execute("UPDATE plans SET status = 'stale' WHERE id = ?", (plan_id,))
        connection.commit()
        connection.close()
        deleted = self.client.delete("/api/v1/plans/%s" % plan_id, headers=headers)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/plans/%s" % plan_id).status_code, 404)

    def test_delete_plan_blocked_while_scan_run_has_open_reviews(self):
        csrf = self.login_default()
        base = Path(self.temporary_directory.name)
        source = base / "review-source"
        library = base / "review-library"
        source.mkdir()
        library.mkdir()
        import sqlite3

        from autoanime_v3.domain.entities import CreateProfile
        from autoanime_v3.services.profiles import ProfileService
        from autoanime_v3.services.roots import RootService
        from autoanime_v3.services.scans import ScanService

        roots = RootService(self.settings.database_path)
        source_root = roots.create_root("source", source)
        library_root = roots.create_root("library", library)
        profile = ProfileService(self.settings.database_path).create_profile(
            CreateProfile("review-profile", source_root.id, library_root.id)
        )
        (source / "计划测试 S01E01.mkv").write_bytes(b"x" * 512)
        outcome = ScanService(self.settings.database_path).run(profile.id)
        connection = sqlite3.connect(str(self.settings.database_path))
        connection.execute(
            """
            INSERT INTO review_items(scan_run_id, review_type, status, dedup_key, payload_json)
            VALUES (?, 'low_confidence', 'open', 'delete-plan-guard-test', '{}')
            """,
            (outcome.scan_run_id,),
        )
        connection.execute(
            "UPDATE plans SET status = 'stale' WHERE id = ?", (outcome.plan_id,)
        )
        connection.commit()
        connection.close()
        blocked = self.client.delete(
            "/api/v1/plans/%s" % outcome.plan_id, headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(blocked.status_code, 422)

    def test_library_shows_search_filter_and_recent_sort(self):
        self.login_default()
        from autoanime_v3.services.changes import ChangeService

        changes = ChangeService(self.settings.database_path)
        changes.create_show("第一个番剧")
        changes.create_show("第二个番剧")
        changes.create_show("电影合集")
        matched = self.client.get("/api/v1/library/shows", params={"q": "番剧"}).json()[
            "items"
        ]
        self.assertEqual(
            [item["canonical_title"] for item in matched],
            ["第一个番剧", "第二个番剧"],
        )
        recent = self.client.get("/api/v1/library/shows", params={"sort": "recent"}).json()[
            "items"
        ]
        self.assertEqual(len(recent), 3)
        self.assertTrue(all("recent_activity" in item for item in recent))
        self.assertTrue(all("season_count" in item and "episode_count" in item for item in recent))

    def test_library_show_detail_includes_episodes_and_file_paths(self):
        self.login_default()
        import sqlite3

        from autoanime_v3.services.changes import ChangeService

        changes = ChangeService(self.settings.database_path)
        show = changes.create_show("季度番剧")
        library = Path(self.temporary_directory.name) / "detail-library"
        library.mkdir()
        connection = sqlite3.connect(str(self.settings.database_path))
        root_id = connection.execute(
            "INSERT INTO storage_roots(kind, path, normalized_path) VALUES ('library', ?, ?)",
            (str(library), str(library).casefold()),
        ).lastrowid
        media_id = connection.execute(
            "INSERT INTO media_files(size, mtime_ns, media_kind) VALUES (100, 1, 'video')"
        ).lastrowid
        season_id = connection.execute(
            "INSERT INTO seasons(show_id, season_number) VALUES (?, 1)", (show.id,)
        ).lastrowid
        episode_id = connection.execute(
            "INSERT INTO episodes(season_id, episode_number, episode_type, sort_value) VALUES (?, '1', 'episode', 1)",
            (season_id,),
        ).lastrowid
        connection.execute(
            "INSERT INTO media_assignments(media_file_id, show_id, season_id, episode_id, source) VALUES (?, ?, ?, ?, 'test')",
            (media_id, show.id, season_id, episode_id),
        )
        destination = library / "季度番剧" / "Season 01" / "S01E01.mkv"
        connection.execute(
            "INSERT INTO file_locations(media_file_id, root_id, path, normalized_path, role, state) VALUES (?, ?, ?, ?, 'library', 'present')",
            (media_id, root_id, str(destination), str(destination).casefold()),
        )
        connection.commit()
        connection.close()
        detail = self.client.get("/api/v1/library/shows/%s" % show.id).json()
        season = detail["seasons"][0]
        self.assertEqual(season["season_number"], 1)
        episode = season["episodes"][0]
        self.assertEqual(episode["episode_number"], "1")
        self.assertTrue(episode["files"])
        self.assertTrue(episode["files"][0]["path"].endswith("S01E01.mkv"))

    def test_settings_include_metadata_block(self):
        csrf = self.login_default()
        view = self.client.get("/api/v1/settings").json()
        metadata = view["metadata"]
        self.assertFalse(metadata["bangumi_enabled"])
        self.assertFalse(metadata["tmdb_enabled"])
        self.assertEqual(metadata["timeout"], 12)
        self.assertFalse(metadata["tmdb_api_key_configured"])
        self.assertFalse(metadata["ready"])

    def test_review_enabled_toggle_and_public_view(self):
        csrf = self.login_default()
        view = self.client.get("/api/v1/settings").json()
        openai = view["openai"]
        self.assertIn("review_enabled", openai)
        self.assertFalse(openai["review_enabled"])

        response = self.client.patch(
            "/api/v1/settings",
            json={
                "key": "review.enabled",
                "value": True,
                "revision": openai["review_enabled_revision"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["value"])
        refreshed = self.client.get("/api/v1/settings").json()
        self.assertTrue(refreshed["openai"]["review_enabled"])

    def test_parse_agent_mode_toggle_and_validation(self):
        csrf = self.login_default()
        view = self.client.get("/api/v1/settings").json()
        openai = view["openai"]
        self.assertIn("parse_agent_mode", openai)
        self.assertEqual(openai["parse_agent_mode"], "off")

        ok = self.client.patch(
            "/api/v1/settings",
            json={
                "key": "parse.agent_mode",
                "value": "all",
                "revision": openai["parse_agent_mode_revision"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["value"], "all")
        refreshed = self.client.get("/api/v1/settings").json()
        self.assertEqual(refreshed["openai"]["parse_agent_mode"], "all")

        bad = self.client.patch(
            "/api/v1/settings",
            json={
                "key": "parse.agent_mode",
                "value": "bogus",
                "revision": openai["parse_agent_mode_revision"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(bad.status_code, 422)

    def test_metadata_toggles_and_timeout_validation(self):
        csrf = self.login_default()
        current = self.client.get("/api/v1/settings").json()["metadata"]
        response = self.client.patch(
            "/api/v1/settings",
            json={
                "key": "metadata.bangumi_enabled",
                "value": True,
                "revision": current["bangumi_enabled_revision"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["value"])

        # 时间过短 -> 422
        short = self.client.patch(
            "/api/v1/settings",
            json={"key": "metadata.timeout", "value": 1, "revision": current["timeout_revision"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(short.status_code, 422)

        # 合法超时 -> 200
        ok = self.client.patch(
            "/api/v1/settings",
            json={"key": "metadata.timeout", "value": 15, "revision": current["timeout_revision"]},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["value"], 15)
        refreshed = self.client.get("/api/v1/settings").json()
        self.assertTrue(refreshed["metadata"]["bangumi_enabled"])

    def test_metadata_tmdb_secret_allowlist(self):
        csrf = self.login_default()
        response = self.client.put(
            "/api/v1/settings/secrets/metadata.tmdb_api_key",
            json={"value": "tmdb-secret-abc"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200)
        refreshed = self.client.get("/api/v1/settings").json()
        self.assertTrue(refreshed["metadata"]["tmdb_api_key_configured"])
        self.assertFalse(any(
            item.get("key") == "metadata.tmdb_api_key"
            for item in refreshed["secrets"]
            if not item.get("configured")
        ))

        unknown = self.client.put(
            "/api/v1/settings/secrets/nope.secret",
            json={"value": "x"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(unknown.status_code, 422)

