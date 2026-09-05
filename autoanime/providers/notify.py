"""通知提供方（E4，拍板 D3：webhook + telegram 最小版）。

- ``WebhookNotifier``：POST JSON ``{category, message, payload}`` 到自配
  URL（企业微信/自建接收端均可接）；
- ``TelegramNotifier``：Bot API ``sendMessage``（密钥 ``SecretStr`` + env，
  绝不进日志/异常文本）；
- ``NotifyDispatcher``：事件订阅过滤（``settings.notify_events`` 白名单，
  事件名如 ``episode.organized`` / ``episode.gap`` / ``upgrade.completed``
  / ``pending.backlog``）+ 多通道扇出；
- ``register_notify``：注册进 Registry（``Notifier`` 协议，"webhook"/
  "telegram"），并返回调度用的 dispatcher；未配置任何通道返回 None。

失败语义：任何网络失败只记日志，**永不向上抛**（通知是旁路，不阻塞
下载/归档主流程）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import SecretStr

from autoanime.config import Settings
from autoanime.core.events import Event
from autoanime.core.interfaces import Notifier, Registry

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class WebhookNotifier:
    """通用 webhook 通道（``client`` 供测试注入 MockTransport）。"""

    def __init__(
        self,
        url: SecretStr,
        *,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.get_secret_value()
        self._timeout_s = timeout_s
        self._client = client

    async def send(self, event: Event) -> None:
        body = {
            "category": event.category.value,
            "message": event.message,
            "payload": event.payload,
        }
        try:
            if self._client is not None:
                await self._client.post(self._url, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    await client.post(self._url, json=body)
        except Exception as exc:  # noqa: BLE001 — 通知永不致命
            logger.warning("webhook notify failed: %s", type(exc).__name__)


class TelegramNotifier:
    """Telegram Bot 通道（token 走 env 的 SecretStr）。"""

    def __init__(
        self,
        bot_token: SecretStr,
        chat_id: str,
        *,
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = bot_token.get_secret_value()
        self._chat_id = chat_id
        self._timeout_s = timeout_s
        self._client = client

    async def send(self, event: Event) -> None:
        payload_tail = json.dumps(event.payload, ensure_ascii=False) if event.payload else ""
        text = f"[AutoAnime][{event.category.value}] {event.message}"
        if payload_tail:
            text = f"{text}\n{payload_tail[:800]}"
        try:
            if self._client is not None:
                await self._client.post(
                    f"{TELEGRAM_API_BASE}/bot{self._token}/sendMessage",
                    json={"chat_id": self._chat_id, "text": text},
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    await client.post(
                        f"{TELEGRAM_API_BASE}/bot{self._token}/sendMessage",
                        json={"chat_id": self._chat_id, "text": text},
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram notify failed: %s", type(exc).__name__)


class NotifyDispatcher:
    """事件白名单过滤 + 多通道扇出（纯进程内，无全局状态）。"""

    def __init__(self, notifiers: list[Any], subscribed_events: list[str]) -> None:
        self._notifiers = notifiers
        self._subscribed = frozenset(subscribed_events)

    @property
    def subscribed_events(self) -> frozenset[str]:
        return self._subscribed

    def wants(self, message: str) -> bool:
        return message in self._subscribed

    async def dispatch(self, event: Event) -> None:
        if not self.wants(event.message) or not self._notifiers:
            return
        for notifier in self._notifiers:
            try:
                await notifier.send(event)
            except Exception as exc:  # noqa: BLE001 — 单通道故障不断扇出
                logger.warning("notify channel failed: %s", type(exc).__name__)

    async def send(self, event: Event) -> None:  # Notifier 协议别名
        await self.dispatch(event)


def register_notify(
    registry: Registry, settings: Settings
) -> NotifyDispatcher | None:
    """按 Settings 装配通知通道并注册进 Registry；未配置返回 None。"""
    if not settings.notify_enabled:
        return None
    notifiers: list[Any] = []
    if settings.notify_webhook_url is not None and settings.notify_webhook_url.get_secret_value():
        webhook = WebhookNotifier(
            settings.notify_webhook_url, timeout_s=settings.notify_timeout_s
        )
        registry.register(Notifier, "webhook")(webhook)
        notifiers.append(webhook)
    if (
        settings.notify_telegram_bot_token is not None
        and settings.notify_telegram_bot_token.get_secret_value()
        and settings.notify_telegram_chat_id
    ):
        telegram = TelegramNotifier(
            settings.notify_telegram_bot_token,
            settings.notify_telegram_chat_id,
            timeout_s=settings.notify_timeout_s,
        )
        registry.register(Notifier, "telegram")(telegram)
        notifiers.append(telegram)
    if not notifiers:
        return None
    return NotifyDispatcher(notifiers, settings.notify_events)
