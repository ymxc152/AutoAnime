"""调度一体化的 ASGI 入口（E4，D16 单 backend 进程）。

``python -m autoanime.api serve``（E2）保持 API-only（开发态语义不变）；
**生产/compose 入口**是本模块的 ``create_app`` 工厂：在 E2 lifespan 之上
组合订阅调度器——

1. 先跑原 lifespan（建 storage/bus/api state）；
2. 复用 ``app.state.storage`` / ``app.state.bus`` 装配 ``LoopComponents``
   （一个进程一个引擎，不重复建连接）；
3. ``run_startup_cycle``（B4 悬挂补扫 + B5 媒体库对账）→
   ``SubscriptionScheduler.start``（RSS 轮询 / 下载轮询 / COLLECTED 降频
   / 通知泵）；
4. 关闭时逆序：scheduler.shutdown → components.close（不关共享 storage）。

compose CMD::

    uvicorn autoanime.scheduler.asgi:create_app --factory --host 0.0.0.0

调度器是本 lifespan 内构造的实例（挂 ``app.state.scheduler``），
进程内单例；不引入模块级可变全局状态。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from autoanime.config import Settings, load_settings
from autoanime.scheduler.scheduler import LoopComponents, SubscriptionScheduler, build_loop

logger = logging.getLogger(__name__)


def _compose_lifespan(app: FastAPI, settings: Settings) -> None:
    original = app.router.lifespan_context

    @asynccontextmanager
    async def combined(target: FastAPI) -> AsyncIterator[None]:
        async with original(target):
            components: LoopComponents = build_loop(
                settings,
                storage=getattr(target.state, "storage", None),
                bus=getattr(target.state, "bus", None),
            )
            scheduler = SubscriptionScheduler(components, settings)
            target.state.scheduler = scheduler
            target.state.loop_components = components
            try:
                await scheduler.run_startup_cycle()
                scheduler.start()
            except Exception:  # noqa: BLE001 — 调度装配失败不拖垮 API
                logger.exception("subscription scheduler failed to start")
            try:
                yield
            finally:
                scheduler.shutdown()
                await components.close()

    app.router.lifespan_context = combined  # type: ignore[assignment]


def create_app(
    settings: Settings | None = None,
    *,
    cors_origins: Sequence[str] | None = None,
) -> FastAPI:
    """FastAPI + AsyncIOScheduler 同进程工厂（D16）。"""
    settings = settings if settings is not None else load_settings()
    app = _build(settings, cors_origins)
    _compose_lifespan(app, settings)
    return app


def _build(settings: Settings, cors_origins: Sequence[str] | None) -> FastAPI:
    from autoanime.web.app import create_app as build_web_app

    return build_web_app(settings, cors_origins=cors_origins)
