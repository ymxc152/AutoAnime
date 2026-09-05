"""L1 aggregator: the fixed-pipeline ``LocalRecognizer``.

The seven dialect modules (T2-T4) are pure functions; this module composes
them into one ``Recognizer`` (autoanime.core.interfaces). Composition is a
fixed pipeline, not a registry: every dialect runs in a deterministic order
on every input, and no module-level mutable state is involved.

Processing stages:

1. Run every dialect in the fixed ``DIALECT_PIPELINE`` order with the same
   ``(raw, context)`` input. Folder merge (conflict downgrade) and the
   ``release_progress`` gate already run inside each dialect; the aggregator
   applies neither a second time.
2. Contract boundary: a dialect that raises ``ValueError`` (its internal
   ``ParseResult`` invariants are unsatisfiable for this input) counts as no
   hit; the Recognizer contract only knows ``ParseResult | None``.
3. Quality gates -- PR3 confidence-contract invariants enforced on every
   candidate regardless of which dialect produced it:

   - a candidate title still containing ASCII square brackets failed title
     extraction and is discarded;
   - a title or fansub that still carries a season marker (``第二季``,
     ``2nd Season``, ``Season 2``) is a misread tag: such a title is
     discarded, such a fansub caps the candidate at MEDIUM;
   - a CJK title whose evidence is not ``folder`` caps at MEDIUM (contract:
     "Chinese title without context");
   - an all-caps multi-token title caps at MEDIUM (dubious title
     reconstruction, mirroring the dialect-A rule);
   - a folder-sourced fansub on a filename that itself contains bracket
     groups caps at MEDIUM: the file carries its own unread group tag, and
     the merge contract prefers filename evidence.

4. Winner selection, all deterministic with no global state: highest
   confidence first; ties broken by the more informative result (more
   populated fields), then by the more filename-grounded result (more
   ``name`` evidence), then by the earlier dialect in the fixed order.
5. No candidate surviving returns ``None``: L1 cannot help and downstream
   (L2/L3) stages take over.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TypeAlias

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.confidence import confidence_for
from autoanime.pipeline.l1.dialects import (
    parse_bracket,
    parse_cjk,
    parse_dot,
    parse_ep,
    parse_minimal,
    parse_pure_bracket,
    parse_special,
)

DialectFn: TypeAlias = Callable[[RawName, ParseContext | None], ParseResult | None]

# Fixed invocation order. It doubles as the final deterministic tie-breaker.
DIALECT_PIPELINE: tuple[DialectFn, ...] = (
    parse_dot,
    parse_bracket,
    parse_pure_bracket,
    parse_cjk,
    parse_ep,
    parse_special,
    parse_minimal,
)

_LEVEL_RANK: dict[Confidence, int] = {
    Confidence.LOW: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
}

# Season markers that must never survive inside a title or a fansub tag:
# they belong to the season field.
_SEASON_RESIDUE_RE = re.compile(
    r"第[0-9一二三四五六七八九十]+季|\d+(?:st|nd|rd|th)\s+Season|Season\s*\d+",
    re.IGNORECASE,
)

# A CJK title that the filename itself (not the folder) provided is a
# dubious reconstruction per the PR3 confidence contract.
_CJK_RE = re.compile(r"[一-鿿]")

# All-caps multi-token titles ("BLACK TORCH"): the acronym/original-case
# split is unknowable from the name alone.
_UPPER_ALPHA_TOKEN_RE = re.compile(r"^[A-Z]+$")

# Structural brackets that must never survive inside a title candidate.
_TITLE_BRACKET_CHARS = frozenset("[]")

_FIELDS: tuple[str, ...] = ("title", "season", "episode", "segment", "fansub")


class LocalRecognizer:
    """Run every L1 dialect in fixed order; keep the best contract-clean hit."""

    def __init__(self, dialects: Sequence[DialectFn] = DIALECT_PIPELINE) -> None:
        self._dialects: tuple[DialectFn, ...] = tuple(dialects)

    async def parse(
        self, raw: RawName, context: ParseContext | None = None
    ) -> ParseResult | None:
        """Parse one release name; ``None`` when no dialect matches."""
        best: ParseResult | None = None
        best_key: tuple[int, ...] | None = None
        for index, dialect in enumerate(self._dialects):
            result = self._run_dialect(dialect, raw, context)
            if result is None:
                continue
            key = _selection_key(result, index)
            if best_key is None or key > best_key:
                best, best_key = result, key
        return best

    @staticmethod
    def _run_dialect(
        dialect: DialectFn, raw: RawName, context: ParseContext | None
    ) -> ParseResult | None:
        try:
            result = dialect(raw, context)
        except ValueError:
            # A dialect whose draft cannot satisfy the ParseResult contract
            # has no hit for this input; it must not crash the pipeline.
            return None
        if result is None:
            return None
        return _apply_quality_gates(result, raw)


def _apply_quality_gates(result: ParseResult, raw: RawName) -> ParseResult | None:
    """Enforce the shared PR3 confidence contract on one dialect hit."""
    if _TITLE_BRACKET_CHARS.intersection(result.title):
        return None  # title extraction failed: brackets leaked into the title
    if _SEASON_RESIDUE_RE.search(result.title):
        return None  # a season marker still inside the title is a misparse

    gated = result
    if _SEASON_RESIDUE_RE.search(gated.fansub or ""):
        gated = _capped(gated, Confidence.MEDIUM)
    if _CJK_RE.search(gated.title) and gated.evidence.get("title") != "folder":
        gated = _capped(gated, Confidence.MEDIUM)
    if gated.level is Confidence.HIGH:
        alpha_tokens = [token for token in gated.title.split() if token.isalpha()]
        if len(alpha_tokens) >= 2 and all(
            _UPPER_ALPHA_TOKEN_RE.fullmatch(token) for token in alpha_tokens
        ):
            gated = _capped(gated, Confidence.MEDIUM)
    if (
        gated.fansub
        and gated.evidence.get("fansub") == "folder"
        and _TITLE_BRACKET_CHARS.intersection(raw.name)
    ):
        gated = _capped(gated, Confidence.MEDIUM)
    return gated


def _capped(result: ParseResult, cap: Confidence) -> ParseResult:
    """A copy of ``result`` whose level does not exceed ``cap``."""
    if _LEVEL_RANK[result.level] <= _LEVEL_RANK[cap]:
        return result
    return replace(result, level=cap, confidence=confidence_for(cap))


def _selection_key(result: ParseResult, index: int) -> tuple[int, int, int, int]:
    """Higher is better; the fixed-order index is the final tie-breaker."""
    populated = sum(getattr(result, field) is not None for field in _FIELDS)
    name_evidence = sum(source == "name" for source in result.evidence.values())
    return (
        _LEVEL_RANK[result.level],
        populated,
        name_evidence,
        -index,
    )
