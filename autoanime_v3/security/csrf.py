"""CSRF token validation kept separate for API dependency reuse."""

from .sessions import token_matches


def csrf_matches(token, expected_hash):
    return token_matches(token, expected_hash)

