"""外部能力 provider 适配层（PR5）。

本包只承载**外部能力**的实现并注册进 Registry（PR5 规则 7）：

- ``llm``: ``HttpxLlmTransport``——OpenAI 兼容 chat completions transport，
  注册名 ``"openai"``；仅在 ``llm_enabled`` 且 ``llm_base_url`` 配置齐全时
  注册。

prompt/解析/预算等纯函数组件在 ``autoanime.pipeline.l3``，不进 registry；
LlmCacheStore（DB 版）属 store 层（T2），不在本包注册。

注册是显式动作：由装配方（CLI/web 装配代码，T5）持有一个 ``Registry``
实例并调用 ``register_providers``；本包不创建模块级全局 registry（PR5
规则 4）。
"""

from __future__ import annotations

import logging

from autoanime.config import Settings
from autoanime.core.interfaces import LlmTransport, Registry
from autoanime.providers.llm import HttpxLlmTransport, LlmTransportError, safe_origin

logger = logging.getLogger(__name__)

LLM_TRANSPORT_NAME = "openai"

__all__ = [
    "LLM_TRANSPORT_NAME",
    "HttpxLlmTransport",
    "LlmTransportError",
    "register_providers",
    "safe_origin",
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
