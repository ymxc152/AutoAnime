import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


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

    def bootstrap_and_login(self):
        bootstrap = self.client.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": "Correct Horse Battery Staple!42"},
        )
        self.assertEqual(bootstrap.status_code, 201)
        response = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Correct Horse Battery Staple!42"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def test_health_bootstrap_login_me_and_logout(self):
        self.assertEqual(self.client.get("/health/live").json()["status"], "live")
        self.assertEqual(self.client.get("/health/ready").json()["status"], "ready")
        csrf = self.bootstrap_and_login()
        cookie = self.client.cookies.get("autoanime_session")
        self.assertTrue(cookie)
        me = self.client.get("/api/v1/auth/me")
        self.assertEqual(me.json()["username"], "admin")
        logout = self.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    def test_authenticated_root_creation_and_listing(self):
        csrf = self.bootstrap_and_login()
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

    def test_remote_client_cannot_claim_first_administrator(self):
        with TestClient(self.app, client=("203.0.113.10", 50000)) as remote:
            response = remote.post(
                "/api/v1/auth/bootstrap",
                json={"username": "attacker", "password": "Correct Horse Battery Staple!42"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "bootstrap_local_only")
        self.assertEqual(
            self.client.get("/api/v1/auth/bootstrap-status").json(),
            {"configured": False},
        )


if __name__ == "__main__":
    unittest.main()
