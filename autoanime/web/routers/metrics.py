"""Dashboard 指标（/api/metrics）：纯读侧聚合，映射到 MetricsOut。"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter

from autoanime.web.deps import ApiStoreDep
from autoanime.web.schemas import (
    CurvePointOut,
    LevelStatsOut,
    MemorySourceStatsOut,
    MetricsOut,
    PendingTrendPointOut,
)

router = APIRouter(tags=["metrics"])

TREND_DAYS = 28
WEEKS = 8


def _iso_week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _weekly_llm_curve(
    daily: dict[date, tuple[int, int]], weeks: int = WEEKS
) -> list[CurvePointOut]:
    """LLM 调用率周曲线：最近 N 个 ISO 周（含当周），无数据周补零。"""
    today = date.today()
    start = today - timedelta(weeks=weeks - 1, days=today.weekday())
    buckets: dict[str, tuple[int, int]] = {}
    for offset in range(weeks):
        week_start = start + timedelta(weeks=offset)
        buckets[_iso_week_key(week_start)] = (0, 0)
    for day, (total, llm_called) in daily.items():
        key = _iso_week_key(day)
        if key in buckets:
            base_total, base_llm = buckets[key]
            buckets[key] = (base_total + total, base_llm + llm_called)
    return [
        CurvePointOut(
            bucket=key,
            total=total,
            llm_called=llm_called,
            llm_rate=(llm_called / total) if total > 0 else None,
        )
        for key, (total, llm_called) in buckets.items()
    ]


def _pending_trend(
    created: dict[date, int], resolved: dict[date, int], days: int = TREND_DAYS
) -> list[PendingTrendPointOut]:
    today = date.today()
    points: list[PendingTrendPointOut] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        points.append(
            PendingTrendPointOut(
                bucket=day.isoformat(),
                created=created.get(day, 0),
                resolved=resolved.get(day, 0),
            )
        )
    return points


@router.get("/metrics", response_model=MetricsOut)
async def get_metrics(store: ApiStoreDep) -> MetricsOut:
    snapshot = await store.metrics_snapshot(trend_days=TREND_DAYS)
    intervention_rate: float | None = None
    if snapshot.audit_total > 0:
        intervention_rate = snapshot.audit_manual / snapshot.audit_total
    return MetricsOut(
        intervention_rate=intervention_rate,
        audit_total=snapshot.audit_total,
        audit_manual=snapshot.audit_manual,
        by_level=[
            LevelStatsOut(
                level=int(item["level"]),  # type: ignore[arg-type]
                total=int(item["total"]),  # type: ignore[arg-type]
                llm_called=int(item["llm_called"]),  # type: ignore[arg-type]
                outcomes=dict(item["outcomes"]),  # type: ignore[arg-type]
            )
            for item in snapshot.by_level
        ],
        llm_call_curve_weekly=_weekly_llm_curve(snapshot.daily_parse_events),
        pending_trend_daily=_pending_trend(
            snapshot.daily_pending_created, snapshot.daily_pending_resolved
        ),
        pending_open=snapshot.pending_open,
        episode_states=snapshot.episode_states,
        memory_sources=[
            MemorySourceStatsOut(
                source=str(item["source"]),
                status=str(item["status"]),
                rows=int(item["rows"]),  # type: ignore[arg-type]
            )
            for item in snapshot.memory_sources
        ],
    )
