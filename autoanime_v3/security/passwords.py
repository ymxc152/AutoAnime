"""Argon2id password hashing."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(password):
    return _HASHER.hash(password)


def verify_password(password_hash, password):
    try:
        return bool(_HASHER.verify(password_hash, password))
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash):
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True

