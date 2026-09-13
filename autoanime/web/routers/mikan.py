"""Mikan 字幕组 RSS 发现端点：GET /api/mikan/subtitle_groups?title=…。

选番抽屉「自动获取 RSS」的数据源：标题 → Mikan 搜索最佳匹配 → 字幕组
公开 RSS 列表。只读、无副作用、不落库；外呼经 gateway/mikan.py（每请求
一次性 httpx client，trust_env 复用 HTTPS_PROXY 代理对策，超时复用
``rss_fetch_timeout_s``）。失败语义：搜索无命中 404；网络/上游异常 502
（detail 只含 host 与原因类别，不含任何 URL/密钥）。audit 只记「查询了
字幕组发现」与标题长度，不记标题本身与任何外链 URL。
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from autoanime.gateway.mikan import (
    MikanGroupOption,
    MikanLookupError,
    MikanLookupMiss,
    resolve_subtitle_groups,
)
from autoanime.web.deps import GovernanceDep, SettingsDep

router = APIRouter(tags=["mikan"])


class MikanGroupOptionOut(BaseModel):
    group_id: str
    group_name: str
    rss_url: str


class MikanGroupsOut(BaseModel):
    """发现结果:匹配到的 Mikan 番剧 + 可选字幕组 RSS 列表。"""

    matched_title: str
    bangumi_id: int
    groups: list[MikanGroupOptionOut]


def _out(
    hit_title: str, bangumi_id: int, groups: tuple[MikanGroupOption, ...]
) -> MikanGroupsOut:
    return MikanGroupsOut(
        matched_title=hit_title,
        bangumi_id=bangumi_id,
        groups=[
            MikanGroupOptionOut(
                group_id=g.group_id, group_name=g.group_name, rss_url=g.rss_url
            )
            for g in groups
        ],
    )


@router.get("/mikan/subtitle_groups", response_model=MikanGroupsOut)
async def mikan_subtitle_groups(
    settings: SettingsDep,
    governance: GovernanceDep,
    title: Annotated[
        str, Query(min_length=1, max_length=120, description="番剧标题(中/日文均可)")
    ],
) -> MikanGroupsOut:
    """按标题发现 Mikan 字幕组 RSS 选项(搜索首个命中视为最佳匹配)。

    硬上限 = 2×rss_fetch_timeout_s(httpx 读超时挡不住代理慢滴流,实测可拖
    数分钟——wait_for 保证最坏 20s 左右返回 502,抽屉侧快速进入重试/手动态)。
    """
    deadline_s = settings.rss_fetch_timeout_s * 2
    try:
        async with httpx.AsyncClient(
            timeout=settings.rss_fetch_timeout_s, follow_redirects=True
        ) as client:
            hit, groups = await asyncio.wait_for(
                resolve_subtitle_groups(client, title), timeout=deadline_s
            )
    except TimeoutError:
        raise HTTPException(status_code=502, detail="mikan: timeout") from None
    except MikanLookupMiss as exc:
        raise HTTPException(status_code=404, detail=f"mikan: {exc.detail}") from None
    except MikanLookupError as exc:
        raise HTTPException(status_code=502, detail=f"mikan: {exc.detail}") from None
    await governance.record_audit(
        operation_id=uuid4().hex,
        entity="rss_sources",
        action="mikan_groups_viewed",
        instruction={"title_len": len(title), "group_count": len(groups)},
    )
    return _out(hit.title, hit.bangumi_id, groups)
