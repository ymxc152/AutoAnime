"""L3 LLM fallback 识别器（PR5 T3）：cache → prompt → transport → schema → draft.

决策流程（PR5 契约定版）：

1. ``llm_enabled`` 关闭或缺 ``llm_model`` → 返回 ``None``（不可用，R7）；
2. 先查 llm_cache（``llm_cache_key(raw.name)``，与 bypass 同源）；
   缓存命中则回放同一套严格 schema 解析，解析失败按**脏缓存**处理：
   记日志后当 miss 继续真实调用（防脏缓存永久污染），成功后覆盖写回；
3. 未命中 → ``build_prompt`` 构建首轮 prompt → ``transport.complete``
   （一次真实调用）→ ``parse_llm_response`` 严格解析；
4. 网络类失败（transport 抛错）按 ``transport_retry_allowed`` 重试，
   上限 ``LLM_MAX_RETRIES``，重试不计入真实调用次数；
5. schema 违规按 ``schema_correction_allowed`` 以
   ``build_correction_prompt`` 纠正重试，上限
   ``LLM_SCHEMA_CORRECTION_RETRIES``，再失败放弃并计数；
6. 只有 schema 合法的真实调用响应写 cache（纠正重试成功的结果同样入
   cache；缓存命中不走 transport、不写 cache）；每个 raw_name 每轮最多
   一次真实调用（重试除外）；
7. 预算走 ``budget_exceeded``：``calls_used`` 超限只记 audit 日志，
   **不阻断**调用；
8. 不可用 / 重试耗尽 / 解析失败一律返回 ``None`` 并累计 audit 计数，
   交回 orchestrator 按 L1/L2 原结果路由。

prompt/解析/预算/缓存键纯函数直接复用 ``pipeline.l3``（T1），本模块不
重新实现；网络只在注入的 ``LlmTransport`` 内发生（本模块无 I/O 之外
的副作用），cache 读写只走 ``LlmCacheStore`` Protocol（DB 版在 T2）。
"""

from __future__ import annotations

import logging

from autoanime.config import Settings
from autoanime.core.interfaces import (
    LlmCacheStore,
    LlmTransport,
    ParseContext,
    ParseResult,
    RawName,
)
from autoanime.pipeline.l3 import (
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_S,
    LlmCache,
    LlmResponseError,
    budget_exceeded,
    build_correction_prompt,
    build_prompt,
    l3_parse_result,
    llm_cache_key,
    parse_llm_response,
    schema_correction_allowed,
    transport_retry_allowed,
)

logger = logging.getLogger(__name__)

__all__ = ["LlmFallbackRecognizer"]


class LlmFallbackRecognizer:
    """L3Recognizer 契约的 LLM fallback 实现。

    构造参数来自 Settings（``from_settings``）；``transport`` 与
    ``cache_store`` 由调用方注入（外部能力 transport 经 Registry 解析，
    cache store 为 store 层实现或测试 fake）。实例内的调用计数仅用于
    audit，不是模块级全局状态。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        model: str | None = None,
        timeout_s: float = LLM_TIMEOUT_S,
        budget: int | None = None,
        max_retries: int = LLM_MAX_RETRIES,
    ) -> None:
        self._enabled = enabled
        self._model = model
        self._timeout_s = timeout_s
        self._budget = budget
        self._max_retries = max_retries
        # audit 计数（实例级，非全局）：真实调用数 / 不可用放弃数 / 解析失败数
        self._calls_used = 0
        self._unavailable_count = 0
        self._parse_failure_count = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> LlmFallbackRecognizer:
        """按 Settings 构建（Settings 只读；api_key 不经过本类）。"""
        return cls(
            enabled=settings.llm_enabled,
            model=settings.llm_model,
            timeout_s=settings.llm_timeout_s,
            budget=settings.llm_budget,
            max_retries=settings.llm_max_retries,
        )

    @property
    def calls_used(self) -> int:
        """已用真实 LLM 调用数（重试不计），供 audit。"""
        return self._calls_used

    @property
    def unavailable_count(self) -> int:
        """transport 重试耗尽放弃的次数，供 audit。"""
        return self._unavailable_count

    @property
    def parse_failure_count(self) -> int:
        """schema 纠正后仍失败放弃的次数，供 audit。"""
        return self._parse_failure_count

    async def enhance(
        self,
        raw: RawName,
        result: ParseResult | None,
        context: ParseContext | None,
        transport: LlmTransport,
        cache_store: LlmCacheStore,
        *,
        operation_id: str | None = None,
    ) -> ParseResult | None:
        """L3 主流程；成功返回独立 L3 ParseResult，失败/不可用返回 ``None``。"""
        if not self._enabled or self._model is None:
            logger.debug("llm l3 unavailable: disabled or model missing, op=%s", operation_id)
            return None

        pattern_hash = llm_cache_key(raw.name)
        cached = await self._safe_get(cache_store, pattern_hash, operation_id)
        if cached is not None:
            try:
                draft = parse_llm_response(cached.response)
            except LlmResponseError as exc:
                # 脏缓存：按 miss 继续真实调用，成功后覆盖写回。
                logger.warning(
                    "llm cache dirty (reason=%s), treating as miss, op=%s",
                    exc.reason,
                    operation_id,
                )
            else:
                logger.debug("llm cache hit, op=%s", operation_id)
                return l3_parse_result(draft)

        prompt = build_prompt(raw.name, result, context)
        corrections = 0
        failed_attempts = 0
        counted = False  # 每轮最多计一次真实调用：网络重试与纠正重试不计
        while True:
            try:
                response = await transport.complete(
                    prompt, model=self._model, timeout_s=self._timeout_s
                )
            except Exception as exc:  # noqa: BLE001 - transport 失败一律按网络类降级
                failed_attempts += 1
                if transport_retry_allowed(failed_attempts, max_retries=self._max_retries):
                    logger.debug(
                        "llm transport failed (%s), retrying, op=%s",
                        type(exc).__name__,
                        operation_id,
                    )
                    continue
                self._unavailable_count += 1
                logger.warning(
                    "llm transport unavailable after %d attempts (%s), op=%s",
                    failed_attempts,
                    type(exc).__name__,
                    operation_id,
                )
                return None
            if not counted:
                self._calls_used += 1
                counted = True
                self._audit_budget(operation_id)
            try:
                draft = parse_llm_response(response)
            except LlmResponseError as exc:
                if schema_correction_allowed(corrections):
                    corrections += 1
                    prompt = build_correction_prompt(prompt, response, exc.reason)
                    logger.debug(
                        "llm response invalid (reason=%s), correcting, op=%s",
                        exc.reason,
                        operation_id,
                    )
                    continue
                self._parse_failure_count += 1
                logger.warning(
                    "llm response invalid after correction (reason=%s), op=%s",
                    exc.reason,
                    operation_id,
                )
                return None
            await self._safe_put(
                cache_store,
                LlmCache(pattern_hash=pattern_hash, response=response, model=self._model),
                operation_id,
            )
            return l3_parse_result(draft)

    async def _safe_get(
        self, cache_store: LlmCacheStore, pattern_hash: str, operation_id: str | None
    ) -> LlmCache | None:
        try:
            return await cache_store.get(pattern_hash)
        except Exception as exc:  # noqa: BLE001 - cache 不可用按 miss 降级
            logger.warning(
                "llm cache get failed (%s), treating as miss, op=%s",
                type(exc).__name__,
                operation_id,
            )
            return None

    async def _safe_put(
        self, cache_store: LlmCacheStore, cache: LlmCache, operation_id: str | None
    ) -> None:
        try:
            await cache_store.put(cache)
        except Exception as exc:  # noqa: BLE001 - cache 写失败不影响本次结果
            logger.warning(
                "llm cache put failed (%s), op=%s", type(exc).__name__, operation_id
            )

    def _audit_budget(self, operation_id: str | None) -> None:
        """预算超限只记 audit 日志，绝不阻断（PR5 契约）。"""
        if budget_exceeded(self._calls_used, self._budget):
            logger.warning(
                "llm budget exceeded: calls_used=%d budget=%d (audit only, not blocking), op=%s",
                self._calls_used,
                self._budget,
                operation_id,
            )
