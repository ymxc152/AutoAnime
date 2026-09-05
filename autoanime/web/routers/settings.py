"""Settings 页（GET/PUT /api/settings）：运行时项的读取与进程内覆写。

v1 边界（如实声明）：密钥不回显（只回 has_* 布尔）；PUT 作用于本进程
的 Settings 实例（重启后回到 env/toml 值）——持久化的运行时设置表不在
E2 允许清单（models.py 仅 rss_sources 增量），进报告 backlog。
quality 洗版阈值与自主权限档位依赖 E4/E1 的 config 增量字段，落地后
补进本端点载荷。
"""

from __future__ import annotations

from fastapi import APIRouter

from autoanime.config import Settings
from autoanime.web.deps import SettingsDep
from autoanime.web.schemas import SettingsOut, SettingsUpdateIn

router = APIRouter(prefix="/settings", tags=["settings"])

#: PUT 可覆写的白名单（与 SettingsUpdateIn 字段一致）。
_MUTABLE_FIELDS = frozenset(
    {"dry_run", "l2_enabled", "llm_enabled", "llm_model", "reference_enabled", "reference_order"}
)


def settings_out(settings: Settings) -> SettingsOut:
    return SettingsOut(
        dry_run=settings.dry_run,
        l2_enabled=settings.l2_enabled,
        llm_enabled=settings.llm_enabled,
        llm_model=settings.llm_model,
        reference_enabled=settings.reference_enabled,
        reference_order=list(settings.reference_order),
        library_path=str(settings.library_path),
        download_path=str(settings.download_path),
        api_host=settings.api_host,
        api_port=settings.api_port,
        api_cors_dev_origins=list(settings.api_cors_dev_origins),
        api_sse_heartbeat_s=settings.api_sse_heartbeat_s,
        api_sse_replay_limit=settings.api_sse_replay_limit,
        has_api_token=bool(settings.api_token.get_secret_value()),
        has_llm_api_key=settings.llm_api_key is not None,
    )


@router.get("", response_model=SettingsOut)
async def get_settings(settings: SettingsDep) -> SettingsOut:
    return settings_out(settings)


@router.put("", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdateIn, settings: SettingsDep
) -> SettingsOut:
    fields = body.model_dump(exclude_none=True)
    for key, value in fields.items():
        if key not in _MUTABLE_FIELDS:
            continue
        setattr(settings, key, value)
    return settings_out(settings)
