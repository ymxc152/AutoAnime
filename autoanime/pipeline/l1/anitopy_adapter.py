"""Typed wrapper around the anitopy filename parser.

anitopy is the shared parsing engine for the L1 dialects; this adapter keeps
its untyped dict output out of dialect code and swallows parser crashes so a
single pathological name can never break the pipeline.
"""

from __future__ import annotations

import anitopy


def parse_with_anitopy(text: str) -> dict[str, str]:
    """Parse a release name; returns an empty dict when anitopy fails."""
    try:
        result = anitopy.parse(text)
    except Exception:  # noqa: BLE001 - anitopy raises bare Exception subclasses
        return {}
    if not isinstance(result, dict):
        return {}
    return {str(key): str(value) for key, value in result.items() if value is not None}
