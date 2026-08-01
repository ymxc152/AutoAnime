"""Opaque session-token creation and hashing."""

import hashlib
import hmac
import secrets


def random_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token, expected_hash):
    return hmac.compare_digest(token_hash(token), str(expected_hash))

