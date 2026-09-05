"""providers.notify 单测（E4b）：webhook/telegram 最小版 + 白名单扇出（全部离线）。"""

from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from autoanime.config import Settings
from autoanime.core.events import Event, EventCategory
from autoanime.core.interfaces import Notifier, Registry
from autoanime.providers.notify import (
    NotifyDispatcher,
    TelegramNotifier,
    WebhookNotifier,
    register_notify,
)


def _event(message: str = "episode.organized") -> Event:
    return Event(category=EventCategory.ORGANIZE, message=message, payload={"episode_id": 1})


async def test_webhook_posts_json_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = WebhookNotifier(SecretStr("http://hook.local/notify"), client=client)
    await notifier.send(_event())
    await client.aclose()
    assert seen["path"] == "/notify"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["message"] == "episode.organized"


async def test_telegram_sends_message_and_keeps_token_in_path() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier = TelegramNotifier(SecretStr("tok"), "42", client=client)
    await notifier.send(_event())
    await client.aclose()
    assert seen["path"] == "/bottok/sendMessage"


async def test_send_failures_never_raise() -> None:
    class ExplodingNotifier:
        async def send(self, event: Event) -> None:
            raise RuntimeError("boom")

    dispatcher = NotifyDispatcher([ExplodingNotifier()], ["episode.organized"])
    await dispatcher.dispatch(_event())  # 不抛即通过


async def test_dispatcher_filters_by_whitelist() -> None:
    sent: list[str] = []

    class Recorder:
        async def send(self, event: Event) -> None:
            sent.append(event.message)

    dispatcher = NotifyDispatcher([Recorder()], ["episode.organized", "upgrade.completed"])
    assert dispatcher.wants("episode.organized")
    assert not dispatcher.wants("download.picked")
    await dispatcher.dispatch(_event("download.picked"))
    assert sent == []
    await dispatcher.dispatch(_event("episode.organized"))
    assert sent == ["episode.organized"]


async def test_register_notify_wires_channels_from_settings() -> None:
    settings = Settings(
        notify_enabled=True,
        notify_webhook_url=SecretStr("http://hook.local"),
        notify_telegram_bot_token=SecretStr("tok"),
        notify_telegram_chat_id="42",
        notify_events=["episode.organized"],
    )
    registry = Registry()
    dispatcher = register_notify(registry, settings)
    assert dispatcher is not None
    assert dispatcher.wants("episode.organized")
    # Notifier 协议双通道注册
    assert isinstance(registry.get(Notifier, "webhook"), WebhookNotifier)
    assert isinstance(registry.get(Notifier, "telegram"), TelegramNotifier)


def test_register_notify_disabled_or_unconfigured() -> None:
    registry = Registry()
    assert register_notify(registry, Settings(notify_enabled=False)) is None
    assert register_notify(registry, Settings(notify_enabled=True)) is None


def test_secrets_do_not_leak_into_dispatcher() -> None:
    settings = Settings(
        notify_enabled=True,
        notify_telegram_bot_token=SecretStr("super-secret"),
        notify_telegram_chat_id="42",
    )
    registry = Registry()
    dispatcher = register_notify(registry, settings)
    assert dispatcher is not None
    assert "super-secret" not in repr(dispatcher)
