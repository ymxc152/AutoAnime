/*
 * Dashboard —— 指标卡(人工介入率/待确认队列/LLM 调用率)+ 三级统计 + LLM 周曲线 + 库内集状态。
 * 数据:GET /api/metrics(对齐后端 MetricsOut:intervention_rate/by_level/
 * llm_call_curve_weekly/pending_open/episode_states)。
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

/** LLM 调用周曲线柱状图(8 个 ISO 周),手绘 SVG,无图表依赖 */
function WeeklyCurve({ points }: { points: Metrics['llm_call_curve_weekly'] }) {
  const max = Math.max(1, ...points.map((p) => p.llm_called))
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
          const barH = (p.llm_called / max) * height
          return (
            <g key={p.bucket}>
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
                {p.bucket.slice(5)}
              </text>
              <text
                x={x + barWidth / 2}
                y={height - barH - 3}
                textAnchor="middle"
                className="fill-[var(--ink-text-secondary)] text-[9px]"
              >
                {p.llm_called > 0 ? p.llm_called : ''}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

/** 单级统计行:解析数 + LLM 调用数 */
function LevelRow({
  label,
  total,
  llmCalled,
}: {
  label: string
  total: number
  llmCalled: number
}) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line py-1.5 last:border-b-0">
      <span className="text-sm text-ink">{label}</span>
      <span className="flex items-center gap-2">
        <span className="text-xs text-ink-secondary data-text">
          {strings.dashboard.llmCallsShort} {llmCalled}
        </span>
        <Badge>{total}</Badge>
      </span>
    </div>
  )
}

const LEVEL_LABELS: Record<number, string> = {
  1: strings.dashboard.levelL1,
  2: strings.dashboard.levelL2,
  3: strings.dashboard.levelL3,
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

  // LLM 调用率:全级别汇总(0 解析时按无数据显示)
  const totalParsed = data.by_level.reduce((sum, item) => sum + item.total, 0)
  const totalLlm = data.by_level.reduce((sum, item) => sum + item.llm_called, 0)
  const llmRate = totalParsed > 0 ? totalLlm / totalParsed : null

  return (
    <>
      <PageTitle title={strings.dashboard.title} description={strings.app.tagline} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MetricCard
          label={strings.dashboard.manualInterventionRate}
          value={data.intervention_rate === null ? '—' : formatPercent(data.intervention_rate)}
          hint={`${strings.dashboard.auditManual} ${data.audit_manual} / ${strings.dashboard.auditTotal} ${data.audit_total}`}
        />
        <MetricCard
          label={strings.dashboard.pendingQueue}
          value={String(data.pending_open)}
          hint={strings.dashboard.pendingQueueUnit}
        />
        <MetricCard
          label={strings.dashboard.llmCallRate}
          value={llmRate === null ? '—' : formatPercent(llmRate)}
          hint={`${totalLlm} / ${totalParsed}`}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Card title={strings.dashboard.levelHits} flush>
          <div className="px-4 pb-3">
            {[1, 2, 3].map((level) => {
              const item = data.by_level.find((row) => row.level === level)
              return (
                <LevelRow
                  key={level}
                  label={LEVEL_LABELS[level] ?? `L${level}`}
                  total={item?.total ?? 0}
                  llmCalled={item?.llm_called ?? 0}
                />
              )
            })}
          </div>
        </Card>
        <Card title={strings.dashboard.weeklyCurve}>
          <WeeklyCurve points={data.llm_call_curve_weekly} />
        </Card>
      </div>

      <Card title={strings.dashboard.episodeStates} flush>
        <div className="flex flex-wrap gap-2 px-4 py-3">
          {Object.entries(data.episode_states).length === 0 ? (
            <p className="text-sm text-ink-secondary">{strings.dashboard.noData}</p>
          ) : (
            Object.entries(data.episode_states).map(([state, count]) => (
              <Badge key={state} mark>
                <span className="data-text">
                  {state} {count}
                </span>
              </Badge>
            ))
          )}
        </div>
      </Card>
    </>
  )
}
