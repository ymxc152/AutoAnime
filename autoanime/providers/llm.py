"""LlmTransport 的真实实现：OpenAI 兼容 chat completions（PR5 T3）。

本模块是 L3 唯一允许发起网络调用的文件。transport 只做一次补全尝试：
超时由 ``complete`` 的 ``timeout_s`` 参数控制；网络重试与 schema 纠正
重试由 L3Recognizer（pipeline.l3_llm）按 ``pipeline.l3.budget`` 判定——
按 T1 契约，重试与预算不进 transport。

脱敏约定（PR5 规则 12）：

- api_key 以 ``SecretStr`` 持有，只在请求头瞬时取值，绝不进入日志、
  异常消息或 ``repr``；
- 日志与异常里出现的 endpoint 一律是 ``_safe_origin`` 产出的
  scheme + host（去掉 path/query，不含任何凭据），绝不打印完整
  base_url；
- 上游错误响应体不原样透出，只保留状态码与错误类型。

测试离线：``httpx.MockTransport`` 注入录制响应（tests/fixtures/llm）。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

_CHAT_COMPLETIONS_PATH = "/chat/completions"


class LlmTransportError(RuntimeError):
    """transport 层失败（网络/超时/HTTP 状态/响应形状），消息已脱敏。"""


def safe_origin(url: str) -> str:
    """endpoint 的脱敏形式：scheme + host，去掉 path/query/fragment。"""
    parts = urlsplit(url)
    host = parts.hostname or "<unknown-host>"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    scheme = parts.scheme or "https"
    return f"{scheme}://{host}"


class HttpxLlmTransport:
    """基于 httpx.AsyncClient 的 OpenAI 兼容 transport（单次尝试）。"""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client
        self._owns_client = client is None

    def __repr__(self) -> str:
        # 不含 api_key，base_url 只到 origin。
        return f"HttpxLlmTransport(base_url={safe_origin(self._base_url)!r}, api_key=***)"

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str:
        """一次 chat completion；失败抛 ``LlmTransportError``（消息已脱敏）。"""
        client = self._client
        if client is None:
            client = httpx.AsyncClient()
            self._client = client
            self._owns_client = True
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        logger.debug(
            "llm complete: model=%s endpoint=%s timeout_s=%s",
            model,
            safe_origin(self._base_url),
            timeout_s,
        )
        try:
            response = await client.post(
                self._base_url + _CHAT_COMPLETIONS_PATH,
                json=payload,
                headers=headers,
                timeout=timeout_s,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise LlmTransportError(
                f"llm request failed ({type(exc).__name__}) at {safe_origin(self._base_url)}"
            ) from exc
        return _extract_content(data)

    async def aclose(self) -> None:
        """关闭自建的 httpx 客户端（注入的客户端由注入方管理）。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False


def _extract_content(data: Any) -> str:
    """从 chat completion 响应体取首个 choice 的消息文本；形状不对即失败。"""
    if not isinstance(data, dict):
        raise LlmTransportError("llm response is not a JSON object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmTransportError("llm response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LlmTransportError("llm response choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LlmTransportError("llm response choice has no message")
    content = message.get("content")
    if not isinstance(content, str):
        raise LlmTransportError("llm response message content is not a string")
    return content
