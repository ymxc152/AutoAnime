"""Settings 配置中心（12-D）：GET/PUT /api/settings + notify-test / qbit-test。

三档生效语义（docs/12 §12-D）：
- **immediate**：进程内 ``setattr``（v1 六项沿用）+ 落库；
- **scheduler_rebuild**：``setattr`` + 落库 + 触发 scheduler loop 重建
  （``rebuild_running_scheduler``；API-only 进程无 loop，只落库下次启动
  生效）；
- **requires_restart**：连接/密钥类，只落库不进运行时实例（消费方在
  lifespan/loop 装配时固化了引用，运行期替换不安全），响应里标档位。

密钥纪律（强制）：GET 永不回显密钥值，只回 ``has_*`` 布尔；PUT 空串 =
不修改、显式 null = 清除（RSS token 交互惯例）；密钥不进日志、不进
audit 详情（audit 只记「更新了哪些 key」）。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Request
from pydantic import SecretStr

from autoanime.config import (
    Settings,
    apply_db_overrides,
    encode_setting_value,
    parse_db_overrides,
)
from autoanime.core.enums import Actor
from autoanime.core.events import Event, EventCategory
from autoanime.gateway.qbittorrent import GatewayError, QbittorrentGateway
from autoanime.providers.notify import TelegramNotifier, WebhookNotifier
from autoanime.scheduler.scheduler import rebuild_running_scheduler
from autoanime.web.deps import GovernanceDep, SettingsDep, StorageDep
from autoanime.web.schemas import (
    ChannelTestOut,
    NotifyTestOut,
    QbitTestOut,
    SettingEffect,
    SettingsOut,
    SettingsUpdateIn,
    SettingsUpdateOut,
)

router = APIRouter(prefix="/settings", tags=["settings"])

# ---------------------------------------------------------------------------
# PUT 白名单三档（12-D 定稿字段归组）
# ---------------------------------------------------------------------------

#: 立即生效（进程内 setattr，v1 六项 + 12-D 增量）。
_IMMEDIATE_FIELDS = frozenset(
    {
        "dry_run",
        "l2_enabled",
        "llm_enabled",
        "llm_model",
        "reference_enabled",
        "reference_order",
        "llm_timeout_s",
        "llm_max_retries",
        "reference_qps",
        "pending_backlog_alert_threshold",
        "log_level",
    }
)

#: 调度类（PUT 后重建 scheduler loop 生效）。
_SCHEDULER_FIELDS = frozenset(
    {
        "scheduler_enabled",
        "rss_poll_interval_minutes",
        "rss_poll_jitter_pct",
        "download_poll_interval_s",
        "download_max_retries",
        "collected_check_days",
    }
)

#: 重启生效（连接/密钥类：消费方在 lifespan/loop 装配时固化引用）。
_RESTART_FIELDS = frozenset(
    {
        "llm_base_url",
        "llm_api_key",
        "tmdb_api_key",
        "downloader",
        "qbittorrent_host",
        "qbittorrent_port",
        "qbittorrent_username",
        "qbittorrent_password",
        "notify_enabled",
        "notify_webhook_url",
        "notify_telegram_bot_token",
        "notify_telegram_chat_id",
        "notify_events",
        "upgrade_threshold",
        "upgrade_max_per_episode",
        "upgrade_copy_policy",
        "upgrade_skip_size_gb",
        "mismatch_backfill_budget",
        "naming_title_language",
        "rss_fetch_timeout_s",
        "rss_fetch_retries",
    }
)

_ALL_MUTABLE_FIELDS = _IMMEDIATE_FIELDS | _SCHEDULER_FIELDS | _RESTART_FIELDS

#: 密钥字段（GET 只回 has_*；PUT 空串不修改 / null 清除）。
_SECRET_FIELDS = frozenset(
    {
        "llm_api_key",
        "tmdb_api_key",
        "qbittorrent_password",
        "notify_webhook_url",
        "notify_telegram_bot_token",
    }
)


def _effect_for(key: str) -> SettingEffect:
    if key in _SCHEDULER_FIELDS:
        return "scheduler_rebuild"
    if key in _RESTART_FIELDS:
        return "requires_restart"
    return "immediate"


# ---------------------------------------------------------------------------
# 序列化（GET / PUT 响应共用）
# ---------------------------------------------------------------------------


def settings_out(settings: Settings, rows: dict[str, str]) -> SettingsOut:
    """运行时 + DB 覆盖 → 响应载荷。

    重启生效档字段 DB 覆盖优先（显示「重启后将生效的值」）；密钥只回
    ``has_*``：DB 有行 = 已配置（待生效），否则看运行时实例。
    """
    db = parse_db_overrides(rows)

    def _val(key: str) -> Any:
        return db[key] if key in db else getattr(settings, key)

    def _has(key: str) -> bool:
        if key in db:
            return True
        value = getattr(settings, key)
        # SecretStr("")（如 qbittorrent_password 默认值）视为未配置。
        if isinstance(value, SecretStr):
            return value.get_secret_value() != ""
        return value is not None

    return SettingsOut(
        dry_run=settings.dry_run,
        l2_enabled=settings.l2_enabled,
        llm_enabled=settings.llm_enabled,
        llm_model=settings.llm_model,
        reference_enabled=settings.reference_enabled,
        reference_order=list(settings.reference_order),
        llm_timeout_s=_val("llm_timeout_s"),
        llm_max_retries=_val("llm_max_retries"),
        reference_qps=_val("reference_qps"),
        pending_backlog_alert_threshold=_val("pending_backlog_alert_threshold"),
        log_level=_val("log_level"),
        scheduler_enabled=_val("scheduler_enabled"),
        rss_poll_interval_minutes=_val("rss_poll_interval_minutes"),
        rss_poll_jitter_pct=_val("rss_poll_jitter_pct"),
        download_poll_interval_s=_val("download_poll_interval_s"),
        download_max_retries=_val("download_max_retries"),
        collected_check_days=_val("collected_check_days"),
        downloader=_val("downloader"),
        qbittorrent_host=_val("qbittorrent_host"),
        qbittorrent_port=_val("qbittorrent_port"),
        qbittorrent_username=_val("qbittorrent_username"),
        notify_enabled=_val("notify_enabled"),
        notify_telegram_chat_id=_val("notify_telegram_chat_id"),
        notify_events=list(_val("notify_events")),
        upgrade_threshold=_val("upgrade_threshold"),
        upgrade_max_per_episode=_val("upgrade_max_per_episode"),
        upgrade_copy_policy=_val("upgrade_copy_policy"),
        upgrade_skip_size_gb=_val("upgrade_skip_size_gb"),
        mismatch_backfill_budget=_val("mismatch_backfill_budget"),
        naming_title_language=_val("naming_title_language"),
        rss_fetch_timeout_s=_val("rss_fetch_timeout_s"),
        rss_fetch_retries=_val("rss_fetch_retries"),
        llm_base_url=_val("llm_base_url"),
        library_path=str(settings.library_path),
        download_path=str(settings.download_path),
        api_host=settings.api_host,
        api_port=settings.api_port,
        api_cors_dev_origins=list(settings.api_cors_dev_origins),
        api_sse_heartbeat_s=settings.api_sse_heartbeat_s,
        api_sse_replay_limit=settings.api_sse_replay_limit,
        has_api_token=bool(settings.api_token.get_secret_value()),
        has_llm_api_key=_has("llm_api_key"),
        has_tmdb_api_key=_has("tmdb_api_key"),
        has_qbittorrent_password=_has("qbittorrent_password"),
        has_notify_webhook_url=_has("notify_webhook_url"),
        has_notify_telegram_bot_token=_has("notify_telegram_bot_token"),
    )


def _merged_settings(settings: Settings, rows: dict[str, str]) -> Settings:
    """测试端点用：运行时 + DB 覆盖的副本（重启生效类字段按待生效值试跑）。"""
    return apply_db_overrides(settings.model_copy(), rows)


# ---------------------------------------------------------------------------
# GET / PUT
# ---------------------------------------------------------------------------


@router.get("", response_model=SettingsOut)
async def get_settings(settings: SettingsDep, storage: StorageDep) -> SettingsOut:
    return settings_out(settings, await storage.list_app_settings())


@router.put("", response_model=SettingsUpdateOut)
async def update_settings(
    body: SettingsUpdateIn,
    request: Request,
    settings: SettingsDep,
    storage: StorageDep,
    governance: GovernanceDep,
) -> SettingsUpdateOut:
    """白名单三档写入：setattr（前两档）+ 落库 + 调度重建钩子。

    密钥语义：空串 = 不修改；显式 null = 清除（删 DB 行，回落 env/toml）。
    audit 只记 key 与档位，**不记任何 value**。
    """
    supplied = body.model_dump(exclude_unset=True)
    applied: dict[str, SettingEffect] = {}
    scheduler_touched = False
    for key, value in supplied.items():
        if key in _SECRET_FIELDS:
            if value is None:
                # 显式 null = 清除覆盖项（运行时实例不动，重启后回落 env）。
                await storage.put_app_setting(key, None)
                applied[key] = _effect_for(key)
                continue
            secret_text = value.get_secret_value()
            if secret_text == "":
                continue  # 空串 = 不修改（前端「留空保持原值」惯例）
            value = secret_text
        elif value is None:
            continue  # 非密钥字段 null = 不修改（保持 v1 行为）
        await storage.put_app_setting(key, encode_setting_value(value))
        applied[key] = _effect_for(key)
        if key in _IMMEDIATE_FIELDS or key in _SCHEDULER_FIELDS:
            setattr(settings, key, value)
            scheduler_touched = scheduler_touched or key in _SCHEDULER_FIELDS

    warnings: list[str] = []
    if scheduler_touched:
        # 调度类变更：重建 loop（API-only 进程内为 no-op；重建失败只警告，
        # 不回滚 PUT——见 rebuild_running_scheduler 的失败收口注释）。
        warnings.extend(await rebuild_running_scheduler(request.app.state, settings))

    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="settings",
        action="settings.updated",
        instruction={"keys": sorted(applied), "effects": dict(sorted(applied.items()))},
        actor=Actor.MANUAL,
    )
    return SettingsUpdateOut(
        **settings_out(settings, await storage.list_app_settings()).model_dump(),
        applied=applied,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# notify-test / qbit-test（按当前配置含未重启的 PUT 值试跑）
# ---------------------------------------------------------------------------


@router.post("/notify-test", response_model=NotifyTestOut)
async def notify_test(
    settings: SettingsDep,
    storage: StorageDep,
    governance: GovernanceDep,
) -> NotifyTestOut:
    """逐通道发测试通知（webhook + telegram），返回成功/失败明细。

    用「运行时 + DB 覆盖」的合并配置构造通道——用户可能在启用通知前先
    测试（``notify_enabled`` 不阻塞手工测试）；未配置的通道直接跳过。
    外呼超时复用 ``notify_timeout_s``；异常只归因到通道明细（类型名），
    不含 URL/token。
    """
    merged = _merged_settings(settings, await storage.list_app_settings())
    results: list[ChannelTestOut] = []
    event = Event(EventCategory.SYSTEM, "settings.notify_test", {"test": True})
    async with httpx.AsyncClient(timeout=merged.notify_timeout_s) as http:
        channels: list[tuple[str, WebhookNotifier | TelegramNotifier]] = []
        webhook_url = merged.notify_webhook_url
        if webhook_url is not None and webhook_url.get_secret_value():
            channels.append(
                ("webhook", WebhookNotifier(webhook_url, timeout_s=merged.notify_timeout_s, client=http))
            )
        bot_token = merged.notify_telegram_bot_token
        if (
            bot_token is not None
            and bot_token.get_secret_value()
            and merged.notify_telegram_chat_id
        ):
            channels.append(
                (
                    "telegram",
                    TelegramNotifier(
                        bot_token,
                        merged.notify_telegram_chat_id,
                        timeout_s=merged.notify_timeout_s,
                        client=http,
                    ),
                )
            )
        for name, notifier in channels:
            try:
                await notifier.send_or_raise(event)
                results.append(ChannelTestOut(channel=name, ok=True))
            except Exception as exc:  # noqa: BLE001 — 失败归明细，不致命
                results.append(ChannelTestOut(channel=name, ok=False, error=type(exc).__name__))

    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="settings",
        action="settings.notify_test",
        instruction={
            "channels": [item.channel for item in results],
            "ok": {item.channel: item.ok for item in results},
        },
        actor=Actor.MANUAL,
    )
    return NotifyTestOut(results=results)


@router.post("/qbit-test", response_model=QbitTestOut)
async def qbit_test(
    settings: SettingsDep,
    storage: StorageDep,
    governance: GovernanceDep,
) -> QbitTestOut:
    """qBittorrent 连接测试：登录尝试 + 读服务端版本，失败返回原因。

    同 notify-test：按「运行时 + DB 覆盖」的合并配置试连（密码改完未重启
    也能先验证）；失败原因复用 GatewayError 文案（只含异常类型与操作名，
    不含密码）。
    """
    merged = _merged_settings(settings, await storage.list_app_settings())
    gateway = QbittorrentGateway(
        merged.qbittorrent_host,
        merged.qbittorrent_port,
        merged.qbittorrent_username,
        merged.qbittorrent_password,
        category=merged.qbittorrent_category,
        timeout_s=merged.qbittorrent_timeout_s,
    )
    try:
        version = await gateway.version()
    except GatewayError as exc:
        await governance.record_audit(
            operation_id=uuid4().hex,
            entity="settings",
            action="settings.qbit_test",
            instruction={"ok": False, "error": type(exc).__name__},
            actor=Actor.MANUAL,
        )
        return QbitTestOut(ok=False, error=str(exc))
    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="settings",
        action="settings.qbit_test",
        instruction={"ok": True},
        actor=Actor.MANUAL,
    )
    return QbitTestOut(ok=True, version=version)


__all__ = ["router", "settings_out"]
