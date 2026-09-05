"""Memory-backed recognition: the L2 query + fusion entry point (PR4 T3).

``MemoryEnhancer`` implements the T1 ``MemoryRecognizer`` protocol. It is a
thin binding over ``autoanime.memory.lookup``: key derivation, status/trust
gating, hit drafting, bypass gating and the evidence/fusion merge all live
there (pure logic plus the ``StorageMemoryStore`` adapter); this module only
fixes the protocol signature the orchestrator (T5) will call.

Contract: input is the L1 ParseResult, the optional parse context and the
injected ``MemoryStore``; output is the enhanced ParseResult on a consumed
hit, or ``None`` when memory has nothing to add (bypassed release, no
participating row) so routing falls back to the L1 result alone.
"""

from __future__ import annotations

from autoanime.core.interfaces import MemoryStore, ParseContext, ParseResult
from autoanime.memory.lookup import enhance_result

__all__ = ["MemoryEnhancer"]


class MemoryEnhancer:
    """L2 memory recognizer: L1 result -> two-level lookup -> hit fusion."""

    async def enhance(
        self,
        result: ParseResult,
        context: ParseContext | None,
        store: MemoryStore,
        *,
        operation_id: str | None = None,
    ) -> ParseResult | None:
        return await enhance_result(result, context, store, operation_id=operation_id)
