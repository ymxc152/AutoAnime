/*
 * Dashboard —— 指标卡(人工介入率/本周归档/LLM 调用率)+ 三级命中 + 近 7 日曲线。
 * 数据:GET /api/metrics。
 */
import { useCallback } from 'react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import { strings } from '../strings'
import { Badge, Card, ErrorState, PageTitle, Skeleton } from '../components'
import { formatPercent } from '../lib/views'
import type { Metrics } from '../api/types'

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string
}) {
  return (
    <Card>
      <p className="text-xs text-ink-secondary">{label}</p>
      <p className="data-text mt-1 text-2xl font-semibold text-ink">{value}</p>
      {hint !== undefined && <p className="mt-0.5 text-xs text-ink-muted">{hint}</p>}
    </Card>
  )
}

/** 近 7 日柱状图(归档)+ 调用点(LLM),手绘 SVG,无图表依赖 */
function WeeklyCurve({ points }: { points: Metrics['weekly_curve'] }) {
  const max = Math.max(1, ...points.map((p) => p.archived))
  const barWidth = 24
  const gap = 10
  const height = 72
  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${points.length * (barWidth + gap)} ${height + 18}`}
        className="w-full min-w-[280px]"
        role="img"
        aria-label={strings.dashboard.weeklyCurve}
      >
        {points.map((p, i) => {
          const x = i * (barWidth + gap)
          const barH = (p.archived / max) * height
          return (
            <g key={p.date}>
              <rect
                x={x}
                y={height - barH}
                width={barWidth}
                height={barH}
                rx={2}
                className="fill-primary"
              />
              <text
                x={x + barWidth / 2}
                y={height + 12}
                textAnchor="middle"
                className="fill-[var(--ink-text-secondary)] text-[9px]"
              >
                {p.date.slice(5)}
              </text>
              <text
                x={x + barWidth / 2}
                y={height - barH - 3}
                textAnchor="middle"
                className="fill-[var(--ink-text-secondary)] text-[9px]"
              >
                {p.archived > 0 ? p.archived : ''}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function LevelRow({ label, count, total }: { label: string; count: number; total: number }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line py-1.5 last:border-b-0">
      <span className="text-sm text-ink">{label}</span>
      <span className="flex items-center gap-2">
        <span className="text-xs text-ink-secondary data-text">
          {total > 0 ? formatPercent(count / total) : '—'}
        </span>
        <Badge>{count}</Badge>
      </span>
    </div>
  )
}

export function DashboardPage() {
  const fetcher = useCallback(() => api.metrics.get(), [])
  const { data, loading, error, reload } = useApi(fetcher)

  if (error !== null) {
    return (
      <>
        <PageTitle title={strings.dashboard.title} />
        <ErrorState message={error} onRetry={reload} />
      </>
    )
  }

  if (loading || data === null) {
    return (
      <>
        <PageTitle title={strings.dashboard.title} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      </>
    )
  }

  return (
    <>
      <PageTitle title={strings.dashboard.title} description={strings.app.tagline} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MetricCard
          label={strings.dashboard.manualInterventionRate}
          value={formatPercent(data.manual_intervention_rate)}
          hint={`待确认 ${data.pending_count} 条`}
        />
        <MetricCard
          label={strings.dashboard.weeklyArchived}
          value={String(data.weekly_archived)}
          hint={strings.dashboard.weeklyArchivedUnit}
        />
        <MetricCard
          label={strings.dashboard.llmCallRate}
          value={formatPercent(data.llm_call_rate)}
          hint={`${data.levels.llm_calls} / ${data.levels.total}`}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Card title={strings.dashboard.levelHits} flush>
          <div className="px-4 pb-3">
            <LevelRow label={strings.dashboard.totalParsed} count={data.levels.total} total={data.levels.total} />
            <LevelRow label={strings.dashboard.l1High} count={data.levels.l1_high} total={data.levels.total} />
            <LevelRow label={strings.dashboard.l2Hit} count={data.levels.l2_hit} total={data.levels.total} />
            <LevelRow label={strings.dashboard.l3Entered} count={data.levels.l3_entered} total={data.levels.total} />
            <LevelRow label={strings.dashboard.llmCalls} count={data.levels.llm_calls} total={data.levels.total} />
          </div>
        </Card>
        <Card title={strings.dashboard.weeklyCurve}>
          <WeeklyCurve points={data.weekly_curve} />
        </Card>
      </div>
    </>
  )
}
