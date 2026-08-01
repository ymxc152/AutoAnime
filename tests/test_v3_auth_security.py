import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.clock = MutableClock()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def auth_service(self, ttl_seconds=3600):
        from autoanime_v3.services.auth import AuthService

        return AuthService(self.database, clock=self.clock, session_ttl_seconds=ttl_seconds)

    def test_bootstrap_creates_exactly_one_administrator(self):
        from autoanime_v3.domain.errors import AlreadyBootstrappedError

        service = self.auth_service()
        admin = service.bootstrap_admin("admin", "Correct Horse Battery Staple!42")
        self.assertEqual(admin.username, "admin")
        self.assertTrue(admin.is_active)
        self.assertFalse(hasattr(admin, "password_hash"))
        with self.assertRaises(AlreadyBootstrappedError):
            service.bootstrap_admin("second", "Another Strong Password!42")

    def test_login_returns_random_session_and_never_exposes_hashes(self):
        service = self.auth_service()
        service.bootstrap_admin("admin", "Correct Horse Battery Staple!42")

        first = service.login("admin", "Correct Horse Battery Staple!42", "127.0.0.1", "test")
        second = service.login("admin", "Correct Horse Battery Staple!42", "127.0.0.1", "test")

        self.assertNotEqual(first.session_token, second.session_token)
        self.assertNotEqual(first.csrf_token, second.csrf_token)
        self.assertFalse(hasattr(first.user, "password_hash"))
        connection = sqlite3.connect(str(self.database))
        try:
            stored = connection.execute(
                "SELECT token_hash, csrf_hash FROM user_sessions ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
        self.assertNotIn(first.session_token, {row[0] for row in stored})
        self.assertNotIn(first.csrf_token, {row[1] for row in stored})

    def test_expired_or_revoked_session_is_rejected(self):
        from autoanime_v3.domain.errors import AuthenticationError

        service = self.auth_service(ttl_seconds=30)
        service.bootstrap_admin("admin", "Correct Horse Battery Staple!42")
        expired = service.login("admin", "Correct Horse Battery Staple!42")
        self.clock.advance(seconds=31)
        with self.assertRaises(AuthenticationError):
            service.authenticate(expired.session_token)

        current = service.login("admin", "Correct Horse Battery Staple!42")
        service.logout(current.session_token)
        with self.assertRaises(AuthenticationError):
            service.authenticate(current.session_token)

    def test_state_changing_request_requires_matching_csrf_token(self):
        from autoanime_v3.domain.errors import CsrfValidationError

        service = self.auth_service()
        service.bootstrap_admin("admin", "Correct Horse Battery Staple!42")
        session = service.login("admin", "Correct Horse Battery Staple!42")

        authenticated = service.require_csrf(session.session_token, session.csrf_token)
        self.assertEqual(authenticated.username, "admin")
        with self.assertRaises(CsrfValidationError):
            service.require_csrf(session.session_token, "wrong-token")

    def test_repeated_login_failures_are_temporarily_throttled(self):
        from autoanime_v3.domain.errors import AuthenticationError, LoginThrottledError

        service = self.auth_service()
        service.bootstrap_admin("admin", "Correct Horse Battery Staple!42")
        for unused in range(5):
            with self.assertRaises(AuthenticationError):
                service.login("admin", "wrong", "192.168.1.8", "browser")
        with self.assertRaises(LoginThrottledError):
            service.login("admin", "Correct Horse Battery Staple!42", "192.168.1.8", "browser")
        self.clock.advance(minutes=16)
        session = service.login(
            "admin", "Correct Horse Battery Staple!42", "192.168.1.8", "browser"
        )
        self.assertEqual(session.user.username, "admin")

    def test_secret_status_never_returns_plaintext_or_ciphertext(self):
        from autoanime_v3.security.secrets import EncryptedFileSecretStore
        from autoanime_v3.services.auth import SecretService

        store = EncryptedFileSecretStore(self.root / "secret-store")
        service = SecretService(self.database, store)
        status = service.set_secret("metadata.api_key", "top-secret-value")

        self.assertTrue(status.configured)
        self.assertEqual(status.key, "metadata.api_key")
        self.assertFalse(hasattr(status, "value"))
        self.assertFalse(hasattr(status, "ciphertext"))
        connection = sqlite3.connect(str(self.database))
        try:
            ciphertext = connection.execute(
                "SELECT ciphertext FROM secret_settings WHERE key = ?",
                ("metadata.api_key",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertNotIn(b"top-secret-value", bytes(ciphertext))
        self.assertEqual(store.unprotect(bytes(ciphertext)), "top-secret-value")


if __name__ == "__main__":
    unittest.main()
