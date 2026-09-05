"""外部能力 provider 适配层（PR5/PR6）。

本包只承载**外部能力**的实现并注册进 Registry（PR5 规则 7）：

- ``llm``: ``HttpxLlmTransport``——OpenAI 兼容 chat completions transport，
  注册名 ``"openai"``；仅在 ``llm_enabled`` 且 ``llm_base_url`` 配置齐全时
  注册。
- 参考源：Bangumi / TMDB 两个 ``MetadataReference`` 插件（PR6），注册名
  与 ``reference_order`` 默认链序一致；装配时可选包一层 ``CachedReference``
  （PR6 P2，剧目级缓存 + token bucket 频控）。

prompt/解析/预算等纯函数组件在 ``autoanime.pipeline.l3``，不进 registry；
LlmCacheStore（DB 版）属 store 层（T2），不在本包注册。

注册是显式动作：由装配方（CLI/web 装配代码，T5）持有一个 ``Registry``
实例并调用 ``register_providers`` / ``register_reference_providers``；
本包不创建模块级全局 registry（PR5 规则 4 / PR6 规则 5）。
"""

from __future__ import annotations

import logging

from autoanime.config import Settings
from autoanime.core.interfaces import LlmTransport, MetadataReference, Registry
from autoanime.memory.reference_cache import (
    CachedReference,
    ReferenceCacheStore,
    TokenBucketLimiter,
)
from autoanime.providers.bangumi import (
    ANIME_SUBJECT_TYPE,
    BANGUMI_BASE_URL,
    USER_AGENT,
    BangumiReference,
)
from autoanime.providers.llm import HttpxLlmTransport, LlmTransportError, safe_origin
from autoanime.providers.notify import NotifyDispatcher, register_notify
from autoanime.providers.tmdb import (
    DEFAULT_LANGUAGE,
    TMDB_API_KEY_ENV,
    TMDB_BASE_URL,
    TmdbReference,
)

logger = logging.getLogger(__name__)

LLM_TRANSPORT_NAME = "openai"

__all__ = [
    "ANIME_SUBJECT_TYPE",
    "BANGUMI_BASE_URL",
    "BangumiReference",
    "DEFAULT_LANGUAGE",
    "CachedReference",
    "LLM_TRANSPORT_NAME",
    "HttpxLlmTransport",
    "LlmTransportError",
    "NotifyDispatcher",
    "ReferenceCacheStore",
    "TMDB_API_KEY_ENV",
    "TMDB_BASE_URL",
    "TmdbReference",
    "TokenBucketLimiter",
    "USER_AGENT",
    "safe_origin",
    "register_notify",
    "register_providers",
    "register_reference_providers",
]


def register_providers(registry: Registry, settings: Settings) -> bool:
    """按 Settings 把外部能力注册进给定 Registry；返回是否注册了 transport。

    ``llm_enabled`` 关闭或 ``llm_base_url``/``llm_model`` 缺失时不注册，
    返回 ``False``（L3 静默降级为不可用，不抛错）。api_key 以 ``SecretStr``
    原样传入 transport，本函数不落日志。
    """
    if not settings.llm_enabled:
        logger.debug("llm transport not registered: llm_enabled is false")
        return False
    if not settings.llm_base_url or not settings.llm_model:
        logger.debug("llm transport not registered: llm_base_url/llm_model missing")
        return False
    transport = HttpxLlmTransport(
        settings.llm_base_url,
        settings.llm_api_key,
    )
    registry.register(LlmTransport, LLM_TRANSPORT_NAME)(transport)
    logger.debug("llm transport registered: name=%s", LLM_TRANSPORT_NAME)
    return True


def register_reference_providers(
    registry: Registry,
    *,
    cache_store: ReferenceCacheStore | None = None,
    reference_qps: float | None = None,
) -> None:
    """把 Bangumi/TMDB 参考源实例注册进显式 Registry。

    注册名与 ``reference_order`` 默认链序（``["bangumi", "tmdb"]``）一致。
    每次调用创建新实例（频控状态等实例状态随装配边界重置）；重复注册
    同名插件按 Registry 语义覆盖。TMDB 未配置 ``AUTOANIME_TMDB_API_KEY``
    时仍注册其实例（``lookup`` 直接 miss，链继续问下一个 provider）。

    ``cache_store`` 提供时（PR6 P2），每个实例包一层 ``CachedReference``：
    chain（PR5 定稿契约）无需改动即拿到带剧目级缓存 + 频控的实例；缺省
    ``None`` 时注册裸 adapter（保持 P1 行为）。``reference_qps`` 为包装层
    token bucket 速率（``Settings.reference_qps``），未配置或非正时不启用
    包装层频控（adapter 内部 HTTP 层节流兜底）。
    """
    providers: list[tuple[str, MetadataReference]] = [
        ("bangumi", BangumiReference()),
        ("tmdb", TmdbReference()),
    ]
    for name, instance in providers:
        wrapped: MetadataReference = instance
        if cache_store is not None:
            limiter = (
                TokenBucketLimiter(qps=reference_qps)
                if reference_qps is not None and reference_qps > 0
                else None
            )
            wrapped = CachedReference(
                provider=name,
                upstream=instance,
                store=cache_store,
                limiter=limiter,
            )
        registry.register(MetadataReference, name)(wrapped)
