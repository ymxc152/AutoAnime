"""Two-level memory key derivation and stable hashing.

Contract (PR4 decisions):
- level 1 (series, the workhorse): the title shape alone -- no fansub, no
  season/episode structure. Cross-fansub hits are the alias table's job.
- level 2 (exact, the fallback): title shape + season/episode structure,
  with the normalized fansub participating in exact-level validation.
- ``key_hash`` is SHA-256 over the UTF-8 key text: same input, same digest,
  stable across processes and platforms.

Pure functions only.
"""

from __future__ import annotations

import hashlib

from autoanime.pipeline.l2.placeholders import build_title_shape

KEY_LEVEL_SERIES = 1
KEY_LEVEL_EXACT = 2

_LEVEL2_TEMPLATE = "{shape}|s={season}|e={episode}|f={fansub}"
_ABSENT = "-"


def stable_hash(text: str) -> str:
    """Stable SHA-256 hex digest of UTF-8 text (same input -> same digest everywhere)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key_hash(key: str) -> str:
    """key_hash contract: the stable digest of the canonical key text."""
    return stable_hash(key)


def fansub_norm(fansub: str | None) -> str | None:
    """Fansub normalized for key participation: trimmed, casefolded, whitespace-folded."""
    if fansub is None:
        return None
    folded = " ".join(fansub.split()).casefold()
    return folded or None


def level1_key(title: str) -> str:
    """Series-level key: the normalized title shape (no fansub, no numbers)."""
    return build_title_shape(title)


def level2_key(
    title: str, season: int | None, episode: int | None, fansub: str | None
) -> str:
    """Exact-level key: title shape + season/episode structure + fansub_norm.

    Absent components are rendered as ``-`` so the key text stays canonical
    and comparable across processes.
    """
    return _LEVEL2_TEMPLATE.format(
        shape=build_title_shape(title),
        season=season if season is not None else _ABSENT,
        episode=episode if episode is not None else _ABSENT,
        fansub=fansub_norm(fansub) or _ABSENT,
    )
