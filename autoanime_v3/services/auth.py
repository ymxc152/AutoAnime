"""Single-administrator authentication and secret-setting services."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.auth import AuthRepository, public_user
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import SecretStatus, SessionCredentials, UserPublic
from autoanime_v3.domain.errors import (
    AlreadyBootstrappedError,
    AuthenticationError,
    CsrfValidationError,
    LoginThrottledError,
    ValidationError,
)
from autoanime_v3.security.csrf import csrf_matches
from autoanime_v3.security.passwords import hash_password, password_needs_rehash, verify_password
from autoanime_v3.security.sessions import random_token, token_hash


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    return datetime.fromisoformat(str(value))


class AuthService:
    failure_limit = 5
    failure_window = timedelta(minutes=15)
    lock_duration = timedelta(minutes=15)

    def __init__(self, database_path, clock=None, session_ttl_seconds=43200):
        self.database_path = Path(database_path)
        self.clock = clock or utc_now
        self.session_ttl = timedelta(seconds=session_ttl_seconds)
        run_migrations(self.database_path)

    def bootstrap_admin(self, username, password):
        username = str(username).strip()
        if len(username) < 3 or len(password) < 12:
            raise ValidationError("Administrator username or password is too short")
        now = iso(self.clock())
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = AuthRepository(uow.connection)
            if repository.user_count() != 0:
                raise AlreadyBootstrappedError("Administrator has already been created")
            row = repository.create_user(username, hash_password(password), now)
            uow.commit()
            return public_user(row)

    def _attempt_key(self, username, client_ip):
        value = "%s\0%s" % (str(username).casefold(), client_ip or "unknown")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _check_throttle(self, repository, attempt_key, now):
        attempt = repository.get_login_attempt(attempt_key)
        if attempt is None or attempt["locked_until"] is None:
            return
        if parse_time(attempt["locked_until"]) > now:
            raise LoginThrottledError(
                "Too many failed login attempts",
                {"retry_after": attempt["locked_until"]},
            )

    def _record_failure(self, repository, attempt_key, now):
        attempt = repository.get_login_attempt(attempt_key)
        if attempt is None or now - parse_time(attempt["window_started_at"]) > self.failure_window:
            count = 1
            window_started = now
        else:
            count = int(attempt["failure_count"]) + 1
            window_started = parse_time(attempt["window_started_at"])
        locked_until = now + self.lock_duration if count >= self.failure_limit else None
        repository.save_login_failure(
            attempt_key,
            count,
            iso(window_started),
            iso(locked_until) if locked_until is not None else None,
            iso(now),
        )

    def login(self, username, password, client_ip=None, user_agent=None):
        now = self.clock()
        attempt_key = self._attempt_key(username, client_ip)
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = AuthRepository(uow.connection)
            self._check_throttle(repository, attempt_key, now)
            user = repository.get_user_by_username(str(username).strip())
            valid = user is not None and bool(user["is_active"]) and verify_password(
                user["password_hash"], password
            )
            if not valid:
                self._record_failure(repository, attempt_key, now)
                uow.commit()
                raise AuthenticationError("Invalid username or password")
            repository.clear_login_attempt(attempt_key)
            if password_needs_rehash(user["password_hash"]):
                uow.connection.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (hash_password(password), iso(now), user["id"]),
                )
            session_token = random_token()
            csrf_token = random_token()
            expires_at = now + self.session_ttl
            repository.create_session(
                user["id"],
                token_hash(session_token),
                token_hash(csrf_token),
                iso(now),
                iso(expires_at),
                client_ip,
                user_agent,
            )
            uow.commit()
            return SessionCredentials(
                session_token=session_token,
                csrf_token=csrf_token,
                expires_at=iso(expires_at),
                user=public_user(user),
            )

    def _session_row(self, repository, session_token, now):
        row = repository.find_session(token_hash(session_token))
        if (
            row is None
            or row["revoked_at"] is not None
            or not bool(row["is_active"])
            or parse_time(row["expires_at"]) <= now
        ):
            raise AuthenticationError("Session is expired or invalid")
        return row

    def authenticate(self, session_token):
        now = self.clock()
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = AuthRepository(uow.connection)
            row = self._session_row(repository, session_token, now)
            repository.touch_session(row["id"], iso(now))
            uow.commit()
            return UserPublic(int(row["user_id"]), str(row["username"]), bool(row["is_active"]))

    def require_csrf(self, session_token, csrf_token):
        now = self.clock()
        with SqliteUnitOfWork(self.database_path) as uow:
            repository = AuthRepository(uow.connection)
            row = self._session_row(repository, session_token, now)
            if not csrf_matches(csrf_token, row["csrf_hash"]):
                raise CsrfValidationError("CSRF token does not match the session")
            repository.touch_session(row["id"], iso(now))
            uow.commit()
            return UserPublic(int(row["user_id"]), str(row["username"]), bool(row["is_active"]))

    def logout(self, session_token):
        with SqliteUnitOfWork(self.database_path) as uow:
            AuthRepository(uow.connection).revoke_session(token_hash(session_token), iso(self.clock()))
            uow.commit()


class SecretService:
    def __init__(self, database_path, secret_store):
        self.database_path = Path(database_path)
        self.secret_store = secret_store
        run_migrations(self.database_path)

    def set_secret(self, key, value):
        if not value:
            raise ValidationError("Secret value cannot be empty")
        ciphertext = self.secret_store.protect(value)
        now = iso(utc_now())
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute(
                """
                INSERT INTO secret_settings(key, ciphertext, provider, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    provider = excluded.provider,
                    updated_at = excluded.updated_at
                """,
                (key, ciphertext, self.secret_store.provider, now),
            )
            uow.commit()
        return SecretStatus(key, True, self.secret_store.provider, now)

    def status(self, key):
        from autoanime_v3.db.engine import connect_sqlite

        connection = connect_sqlite(self.database_path)
        connection.row_factory = __import__("sqlite3").Row
        try:
            row = connection.execute(
                "SELECT key, provider, updated_at FROM secret_settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return SecretStatus(key, False, None, None)
            return SecretStatus(str(row["key"]), True, str(row["provider"]), str(row["updated_at"]))
        finally:
            connection.close()

    def reveal_for_integration(self, key):
        from autoanime_v3.db.engine import connect_sqlite

        connection = connect_sqlite(self.database_path)
        try:
            row = connection.execute(
                "SELECT ciphertext FROM secret_settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            return self.secret_store.unprotect(bytes(row[0]))
        finally:
            connection.close()
