"""Authentication persistence adapter."""

from autoanime_v3.domain.entities import UserPublic


def public_user(row):
    return UserPublic(id=int(row["id"]), username=str(row["username"]), is_active=bool(row["is_active"]))


class AuthRepository:
    def __init__(self, connection):
        self.connection = connection

    def user_count(self):
        return int(self.connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, username, password_hash, now):
        cursor = self.connection.execute(
            """
            INSERT INTO users(username, password_hash, password_changed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, password_hash, now, now, now),
        )
        return self.get_user_by_id(cursor.lastrowid)

    def get_user_by_id(self, user_id):
        return self.connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def get_user_by_username(self, username):
        return self.connection.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()

    def create_session(self, user_id, token_hash, csrf_hash, now, expires_at, client_ip, user_agent):
        cursor = self.connection.execute(
            """
            INSERT INTO user_sessions(
                user_id, token_hash, csrf_hash, created_at, last_seen_at, expires_at,
                client_ip, user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, token_hash, csrf_hash, now, now, expires_at, client_ip, user_agent),
        )
        return cursor.lastrowid

    def find_session(self, token_hash):
        return self.connection.execute(
            """
            SELECT s.*, u.username, u.is_active
            FROM user_sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    def touch_session(self, session_id, now):
        self.connection.execute(
            "UPDATE user_sessions SET last_seen_at = ? WHERE id = ?", (now, session_id)
        )

    def revoke_session(self, token_hash, now):
        return self.connection.execute(
            "UPDATE user_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now, token_hash),
        ).rowcount

    def get_login_attempt(self, attempt_key):
        return self.connection.execute(
            "SELECT * FROM login_attempts WHERE attempt_key = ?", (attempt_key,)
        ).fetchone()

    def save_login_failure(self, attempt_key, count, window_started_at, locked_until, now):
        self.connection.execute(
            """
            INSERT INTO login_attempts(
                attempt_key, failure_count, window_started_at, locked_until, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(attempt_key) DO UPDATE SET
                failure_count = excluded.failure_count,
                window_started_at = excluded.window_started_at,
                locked_until = excluded.locked_until,
                updated_at = excluded.updated_at
            """,
            (attempt_key, count, window_started_at, locked_until, now),
        )

    def clear_login_attempt(self, attempt_key):
        self.connection.execute("DELETE FROM login_attempts WHERE attempt_key = ?", (attempt_key,))

