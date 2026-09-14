"""首次运行设置向导（/api/setup）：status / complete / check-update。

- ``GET /setup/status``：纯读（storage 只读查询 + qB 探测不落任何数据），
  返回向导需要的四类事实——是否已有订阅 / 下载器是否配置 / 是否可达 /
  本地下载目录与 qB 默认保存路径是否对齐。qB 探测整体 2s 预算，任何失败
  降级为 False/None，绝不抛出（status 是 Layout 挂载即查的高频端点）。
- ``POST /setup/complete``：落 ``wizard_done=true`` 覆盖项 + audit 记
  ``setup.wizard_done``（actor=MANUAL，instruction 空字典——向导完成没有
  可追责的载荷）。``wizard_done`` 不在 settings PUT 白名单里，
  ``parse_db_overrides`` 对未知 key 跳过，天然不影响配置合并。
- ``GET /setup/check-update``（批次四最小版）：读 GitHub Releases latest
  对比当前版本。**取的是参考项目 Auto_Bangumi 的 release**——本项目
  （AutoAnime）是 Auto_Bangumi 的产品化重写，用户要看的「新版」即上游
  参考项目的新版本（tag/更新内容），这是有意为之而非取错仓库。

响应模型就地定义（SetupStatusDto / CheckUpdateDto 的后端镜像在
frontend/src/api/types.ts 已预埋）；不改 web/schemas.py（该文件归
settings 批次所有）。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from autoanime.config import apply_db_overrides, encode_setting_value
from autoanime.core.enums import Actor
from autoanime.core.models import Series
from autoanime.gateway.qbittorrent import QbittorrentGateway
from autoanime.web.deps import GovernanceDep, SettingsDep, StorageDep

router = APIRouter(prefix="/setup", tags=["setup"])

# ---------------------------------------------------------------------------
# 响应模型（types.ts SetupStatusDto / CheckUpdateDto 的后端镜像）
# ---------------------------------------------------------------------------


class SetupStatusOut(BaseModel):
    needed: bool
    has_subscription: bool
    downloader_configured: bool
    downloader_reachable: bool | None
    qb_save_path: str | None
    paths_aligned: bool | None


class SetupCompleteOut(BaseModel):
    ok: bool


class CheckUpdateOut(BaseModel):
    current: str
    latest: str | None
    has_update: bool
    changelog_url: str | None
    error: str | None


# ---------------------------------------------------------------------------
# GET /setup/status
# ---------------------------------------------------------------------------

#: qB 探测单步预算（version / default_save_path 各一步）：status 是挂载即查
#: 的高频读端点，不能被 qB 不可达拖成 15s 级请求（网关自身超时是 15s）。
_QB_PROBE_TIMEOUT_S = 2.0


def _paths_aligned(download_path: Path, qb_save_path: str) -> bool | None:
    """本地下载目录 vs qB 默认保存路径（resolve 后 normcase 比对）。

    Windows 文件系统大小写不敏感，``os.path.normcase`` 统一小写后再比，
    防 ``C:\\Downloads`` vs ``c:\\downloads`` 误报不一致。
    """
    try:
        local = os.path.normcase(str(Path(download_path).resolve()))
        remote = os.path.normcase(str(Path(qb_save_path).resolve()))
    except (OSError, RuntimeError, ValueError):
        # resolve 对怪异路径（非法字符/环）可能抛；比对不了 = 当不一致，
        # 让向导走「采用 qB 路径」的兜底交互而不是崩。
        return False
    return local == remote


@router.get("/status", response_model=SetupStatusOut)
async def setup_status(settings: SettingsDep, storage: StorageDep) -> SetupStatusOut:
    """向导状态探测（无副作用）。

    ``needed`` 用「series 表有行」这一简单诚实口径：**有订阅 = 向导的
    使命（先建订阅闭环）已达成**，下载器/路径可以之后在设置页补——
    不引入 wizard_done 参与 needed 判定，避免「跳过向导后提示条永挂」
    或「完成向导但还没订阅却被藏起」两种绑架。
    """
    # 只读探测借 transaction() 上下文拿 session：无写语句，commit 是 no-op。
    async with storage.transaction() as session:
        row = (await session.execute(sa.select(Series.id).limit(1))).first()
    has_subscription = row is not None

    # 运行时 + DB 覆盖合并（与 settings.py 测试端点同款）：向导期间刚 PUT
    # 的连接参数（重启生效档，运行时实例未固化）也能被立即探测到。
    merged = apply_db_overrides(settings.model_copy(), await storage.list_app_settings())
    downloader_configured = bool(merged.qbittorrent_host)

    reachable: bool | None = None
    qb_save_path: str | None = None
    paths_aligned: bool | None = None
    if downloader_configured:
        gateway = QbittorrentGateway(
            merged.qbittorrent_host,
            merged.qbittorrent_port,
            merged.qbittorrent_username,
            merged.qbittorrent_password,
            category=merged.qbittorrent_category,
            timeout_s=merged.qbittorrent_timeout_s,
        )
        try:
            await asyncio.wait_for(gateway.version(), timeout=_QB_PROBE_TIMEOUT_S)
            reachable = True
        except Exception:  # noqa: BLE001 — 探测失败降级，绝不 500（超时/登录/连接全收口）
            reachable = False
        if reachable:
            try:
                qb_save_path = await asyncio.wait_for(
                    gateway.default_save_path(), timeout=_QB_PROBE_TIMEOUT_S
                )
            except Exception:  # noqa: BLE001 — 读不到 save_path 不推翻可达性
                qb_save_path = None
            if qb_save_path:
                paths_aligned = _paths_aligned(merged.download_path, qb_save_path)

    return SetupStatusOut(
        needed=not has_subscription,
        has_subscription=has_subscription,
        downloader_configured=downloader_configured,
        downloader_reachable=reachable,
        qb_save_path=qb_save_path,
        paths_aligned=paths_aligned,
    )


# ---------------------------------------------------------------------------
# POST /setup/complete
# ---------------------------------------------------------------------------

#: 向导完成标记的 app_settings key（encode 后落库；非 Settings 字段，
#: parse_db_overrides 对未知 key 跳过，不进配置合并）。
_WIZARD_DONE_KEY = "wizard_done"


@router.post("/complete", response_model=SetupCompleteOut)
async def setup_complete(
    storage: StorageDep, governance: GovernanceDep
) -> SetupCompleteOut:
    """标记向导完成：``wizard_done=true`` 覆盖项 + audit ``setup.wizard_done``。

    audit 的 instruction 是空字典——完成动作本身没有可记的载荷（连接参数
    由 settings 路由自己的 audit 记录，这里不重复）。
    """
    await storage.put_app_setting(_WIZARD_DONE_KEY, encode_setting_value(True))
    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="setup",
        action="setup.wizard_done",
        instruction={},
        actor=Actor.MANUAL,
    )
    return SetupCompleteOut(ok=True)


# ---------------------------------------------------------------------------
# GET /setup/check-update（批次四最小版）
# ---------------------------------------------------------------------------

#: **参考项目** Auto_Bangumi 的 GitHub latest release。本项目是它的产品化
#: 重写（不是同一发布物）——「检查更新」对比的是上游参考项目的新版本，
#: 供用户判断是否值得跟进上游特性，这是需求原意。
_GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/EstrellaXD/Auto_Bangumi/releases/latest"
)

#: 进程内冷却：与 settings.py 试跑端点同款模式（时间戳存 app.state，check→set
#: 无 await，事件循环下原子；测试夹具每测新建 app 天然隔离）。
_CHECK_UPDATE_COOLDOWN_S = 30.0


def _check_update_allowed(request: Request) -> bool:
    """冷却闸门：同一 app 内 check-update 在冷却期内拒绝（429）。"""
    calls: dict[str, float] = getattr(request.app.state, "_setup_check_calls", {})
    now = time.monotonic()
    last = calls.get("setup-check-update", 0.0)
    calls["setup-check-update"] = now
    request.app.state._setup_check_calls = calls
    return (now - last) >= _CHECK_UPDATE_COOLDOWN_S


def _current_version() -> str:
    """当前包版本（pyproject [project].version 经安装元数据读取）。

    发行环境按 pyproject 构建安装（当前 = 2.0.0.dev0）；元数据不可得
    （未安装的裸源码运行）如实回 "0.0.0"——与任何 tag 都不同，会把
    has_update 报 True，这是取不到版本时的诚实语义。
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("autoanime")
    except PackageNotFoundError:
        return "0.0.0"


@router.get("/check-update", response_model=CheckUpdateOut)
async def check_update(request: Request) -> CheckUpdateOut:
    """对比当前版本与参考项目 latest release（tag）。

    - httpx ``trust_env=True``：走系统代理（与 Mikan/海报下载同款网络语义）；
    - 任何网络/解析异常 → ``has_update=False`` + ``error=异常类型名``，
      绝不 500（检查更新是可失败的可选功能）；
    - ``has_update`` 最小口径：latest 非空且与 current 不同（不做 semver
      比较，批次四以「能看见上游有新版」为目标）。
    """
    if not _check_update_allowed(request):
        raise HTTPException(status_code=429, detail="检查太频繁，请稍后再试")
    current = _current_version()
    try:
        async with httpx.AsyncClient(trust_env=True, timeout=10.0) as client:
            resp = await client.get(
                _GITHUB_LATEST_RELEASE_URL,
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — 网络异常归因到类型名，不致命
        return CheckUpdateOut(
            current=current, latest=None, has_update=False, changelog_url=None, error=type(exc).__name__
        )
    tag = str(payload.get("tag_name") or "").strip()
    changelog_url = payload.get("html_url")
    return CheckUpdateOut(
        current=current,
        latest=tag or None,
        has_update=bool(tag) and tag != current,
        changelog_url=str(changelog_url) if changelog_url else None,
        error=None,
    )


__all__ = ["router"]
