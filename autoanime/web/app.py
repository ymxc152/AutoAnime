"""FastAPI 应用装配（E2 M3 后端）。

- lifespan：创建 ``SqliteStorage``（create_all 幂等）→ 装配 ApiStore /
  MemoryGovernance / InMemoryEventBus / confirm 侧 ReferenceChain →
  shutdown 关闭存储；不引入模块级可变全局状态，实例全部挂 app.state。
- 认证（D6）：``AUTOANIME_API_TOKEN`` 非空时校验 ``X-API-Token`` 头
  （SSE 允许同值 ``?token=`` query，B7）；空串 = 关闭认证。中间件一处实现。
- CORS：``cors_origins`` 非空才挂（serve 的 ``--dev`` 传入
  ``settings.api_cors_dev_origins``，默认放开 localhost:5173）。
- 路由层零业务逻辑：识别/归档/学习走既有 pipeline 与 store 方法。
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from autoanime.config import Settings, load_settings
from autoanime.core.events import InMemoryEventBus
from autoanime.core.interfaces import Registry
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.providers import register_reference_providers
from autoanime.web.queries import ApiStore
from autoanime.web.routers import api_router


def build_reference_chain(settings: Settings, storage: SqliteStorage) -> ReferenceChain | None:
    """confirm/correct 的 alias 回填链（与 CLI confirm 同一装配方式）。

    ``reference_enabled=False`` 时为 ``None``（回填钩子不接线，confirm 行为
    与 PR7 之前一致）；链上的 adapter 客户端懒创建、查询受
    ``ALIAS_BACKFILL_TIMEOUT_S`` 严格超时约束。
    """
    if not settings.reference_enabled:
        return None
    registry = Registry()
    register_reference_providers(
        registry, cache_store=storage, reference_qps=settings.reference_qps
    )
    return ReferenceChain(
        registry, order=settings.reference_order, enabled=settings.reference_enabled
    )


def create_app(
    settings: Settings | None = None,
    *,
    cors_origins: Sequence[str] | None = None,
) -> FastAPI:
    settings = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage = SqliteStorage(settings.database_url)
        await storage.create_all()
        app.state.settings = settings
        app.state.storage = storage
        app.state.api_store = ApiStore(storage)
        app.state.governance = MemoryGovernance(storage)
        app.state.bus = InMemoryEventBus()
        app.state.reference_chain = build_reference_chain(settings, storage)
        try:
            yield
        finally:
            await storage.close()

    app = FastAPI(
        title="AutoAnime",
        version="2.0.0.dev0",
        description="Local-first anime library automation (v2 API)",
        lifespan=lifespan,
    )

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def token_auth(request: Request, call_next):
        expected = settings.api_token.get_secret_value()
        if expected:
            # B7：EventSource 无法带 header，/api/events 允许 token 走 query。
            supplied = request.headers.get("x-api-token") or request.query_params.get("token") or ""
            if not secrets.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401, content={"detail": "invalid or missing API token"}
                )
        return await call_next(request)

    @app.get("/api/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)
    return app
