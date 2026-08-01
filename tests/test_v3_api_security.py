import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        from autoanime_v3.api.app import ServerSettings, create_app
        from autoanime_v3.services.auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME

        self.client = TestClient(
            create_app(
                ServerSettings(root / "web.sqlite3", root, secure_cookies=False)
            ),
            client=("127.0.0.1", 50000),
        )
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD},
        )
        self.csrf = login.json()["csrf_token"]

    def tearDown(self):
        self.client.close()
        self.temporary_directory.cleanup()

    def test_error_envelope_and_csrf_rejection(self):
        response = self.client.post(
            "/api/v1/roots", json={"kind": "source", "path": "C:/missing-csrf"}
        )
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["code"], "csrf_validation_failed")
        self.assertTrue(body["trace_id"])

    def test_secret_update_returns_status_only(self):
        response = self.client.put(
            "/api/v1/settings/secrets/metadata.api_key",
            json={"value": "never-return-this-value"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["configured"])
        serialized = response.text
        self.assertNotIn("never-return-this-value", serialized)
        self.assertNotIn("ciphertext", serialized)


if __name__ == "__main__":
    unittest.main()
