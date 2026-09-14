"""E2 routers 聚合：全部挂 /api 前缀，由 app.include_router 统一注册。"""

from __future__ import annotations

from fastapi import APIRouter

from autoanime.web.routers.audit import router as audit_router
from autoanime.web.routers.calendar import router as calendar_router
from autoanime.web.routers.episodes import router as episodes_router
from autoanime.web.routers.events_sse import router as events_router
from autoanime.web.routers.filesystem import router as filesystem_router
from autoanime.web.routers.metrics import router as metrics_router
from autoanime.web.routers.mikan import router as mikan_router
from autoanime.web.routers.organize import router as organize_router
from autoanime.web.routers.pending import router as pending_router
from autoanime.web.routers.pipeline import router as pipeline_router
from autoanime.web.routers.rss_sources import router as rss_sources_router
from autoanime.web.routers.scheduler import router as scheduler_router
from autoanime.web.routers.series import router as series_router
from autoanime.web.routers.settings import router as settings_router
from autoanime.web.routers.setup import router as setup_router
from autoanime.web.routers.subscriptions import router as subscriptions_router

api_router = APIRouter(prefix="/api")
api_router.include_router(metrics_router)
api_router.include_router(series_router)
api_router.include_router(episodes_router)
api_router.include_router(pending_router)
api_router.include_router(pipeline_router)
api_router.include_router(scheduler_router)
api_router.include_router(organize_router)
api_router.include_router(audit_router)
api_router.include_router(subscriptions_router)
api_router.include_router(rss_sources_router)
api_router.include_router(settings_router)
api_router.include_router(setup_router)
api_router.include_router(calendar_router)
api_router.include_router(mikan_router)
api_router.include_router(events_router)
api_router.include_router(filesystem_router)

__all__ = ["api_router"]
