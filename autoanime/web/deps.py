"""FastAPI 依赖注入（E2）：从 app.state 取 lifespan 装配好的单例。

会话/网络能力全部由 lifespan 装配（storage/governance/bus/reference chain），
依赖只做取值与分页参数校验，不在请求内新建外部资源。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request

from autoanime.config import Settings
from autoanime.core.events import InMemoryEventBus
from autoanime.memory.governance import MemoryGovernance
from autoanime.memory.store import SqliteStorage
from autoanime.organize.poster import PosterService
from autoanime.pipeline.l3 import ReferenceChain
from autoanime.web.queries import ApiStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> SqliteStorage:
    return request.app.state.storage


def get_api_store(request: Request) -> ApiStore:
    return request.app.state.api_store


def get_bus(request: Request) -> InMemoryEventBus:
    return request.app.state.bus


def get_governance(request: Request) -> MemoryGovernance:
    return request.app.state.governance


def get_reference_chain(request: Request) -> ReferenceChain | None:
    """confirm/correct 的 alias 回填链（reference_enabled=False 时为 None）。"""
    return request.app.state.reference_chain


SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[SqliteStorage, Depends(get_storage)]
ApiStoreDep = Annotated[ApiStore, Depends(get_api_store)]
BusDep = Annotated[InMemoryEventBus, Depends(get_bus)]
GovernanceDep = Annotated[MemoryGovernance, Depends(get_governance)]

def get_poster_service(request: Request) -> PosterService:
    """海报兜底下载服务（PR3+；lifespan 装配，测试可整体替换）。"""
    return request.app.state.poster_service


ReferenceChainDep = Annotated[ReferenceChain | None, Depends(get_reference_chain)]
PosterServiceDep = Annotated[PosterService, Depends(get_poster_service)]


@dataclass(frozen=True)
class Pagination:
    """统一 limit/offset 分页参数（所有列表端点共用）。"""

    limit: int
    offset: int


def pagination(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PaginationDep = Annotated[Pagination, Depends(pagination)]
