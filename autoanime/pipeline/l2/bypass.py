"""Bypass-list pattern normalization and matching (pure-function side).

A bypass pattern is a normalized release name: L1 name normalization
(extension/noise/whitespace) plus separator folding plus casefold. The
digest stored in the ``bypass_list`` table is the same stable SHA-256 used
for memory keys. A raw name whose pattern digest is in the bypass list must
not be written to memory and must not take part in fusion.

Pure functions only; the store lookup itself lives in the storage layer (T2).
"""

from __future__ import annotations

from collections.abc import Iterable

from autoanime.pipeline.l1.normalize import normalize_name, separators_to_spaces
from autoanime.pipeline.l2.keys import stable_hash


def normalize_pattern(raw_name: str) -> str:
    """Canonical bypass pattern text for a raw release name."""
    return separators_to_spaces(normalize_name(raw_name)).casefold()


def pattern_hash(raw_name: str) -> str:
    """Stable digest of the normalized bypass pattern."""
    return stable_hash(normalize_pattern(raw_name))


def is_bypassed(digest: str, bypassed_hashes: Iterable[str]) -> bool:
    """Whether a pattern digest is present in the given bypass digests."""
    return digest in frozenset(bypassed_hashes)
