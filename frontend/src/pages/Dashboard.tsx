/*
 * Dashboard —— 指标卡(人工介入率/待确认队列/LLM 调用率)+ 三级统计 + LLM 周曲线 + 库内集状态。
 * 数据:GET /api/metrics(对齐后端 MetricsOut:intervention_rate/by_level/
 * llm_call_curve_weekly/pending_open/episode_states)。
 */
import { useCallback } from 'react'
import { ChartLine, Check, Inbox, Sparkles, UserRoundCog, type LucideIcon } from 'lucide-react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import { useReloadOnCategories } from '../hooks/useReloadOnEvent'
import { strings } from '../strings'
import { Badge, Card, EmptyState, ErrorState, PageTitle, Skeleton } from '../components'
import { episodeStateLabel, episodeStateView, formatPercent } from '../lib/views'
import type { EpisodeState, Metrics } from '../api/types'

function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string
  value: string
  hint?: string
  icon: LucideIcon
}) {
  return (
    <Card className="transition-shadow hover:shadow-soft-md">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-ink-secondary">{label}</p>
        <span
          aria-hidden
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-ink-muted"
        >
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="data-text mt-1 text-3xl font-semibold tracking-tight text-ink">{value}</p>
      {hint !== undefined && <p className="mt-0.5 text-xs text-ink-secondary">{hint}</p>}
    </Card>
  )
}

/** LLM 调用周曲线 sparkline(仅渲染有调用的周),手绘 SVG polyline,无图表依赖 */
function WeeklyCurve({ points }: { points: Metrics['llm_call_curve_weekly'] }) {
  // 空桶(0 调用)无信息量,过滤掉;全空时显示空态
  const active = points.filter((p) => p.llm_called > 0)
  const max = Math.max(1, ...active.map((p) => p.llm_called))
  const spacing = 44
  const height = 72
  if (active.length === 0) {
    return (
      <div className="flex flex-col items-center py-6 text-center">
        <ChartLine aria-hidden className="mb-2 h-8 w-8 text-ink-muted" />
        <EmptyState title={strings.common.empty} />
      </div>
    )
  }
  const pts = active.map((p, i) => ({
    ...p,
    x: i * spacing + spacing / 2,
    y: height - (p.llm_called / max) * (height - 14) - 4,
  }))
  const last = pts.at(-1)
  if (last === undefined) {
    return null
  }
  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${active.length * spacing} ${height + 18}`}
        className="w-full min-w-[280px]"
        role="img"
        aria-label={strings.dashboard.weeklyCurve}
      >
        {/* 折线本体 */}
        <polyline
          points={pts.map((p) => `${p.x},${p.y}`).join(' ')}
          fill="none"
          stroke="var(--ink-primary)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
          className="pointer-events-none"
        />
        {/* 末端小圆点 */}
        <circle
          cx={last.x}
          cy={last.y}
          r="3"
          fill="var(--ink-primary)"
          className="pointer-events-none"
        />
        {pts.map((p) => (
          <g key={p.bucket}>
            {/* 透明命中区覆盖整列,细线也易 hover;<title> 为原生 tooltip */}
            <rect
              x={p.x - spacing / 2}
              y={0}
              width={spacing}
              height={height + 6}
              fill="transparent"
            >
              <title>{`${p.bucket} · LLM 调用 ${p.llm_called} 次`}</title>
            </rect>
            <text
              x={p.x}
              y={height + 12}
              textAnchor="middle"
              className="fill-[var(--ink-text-secondary)] text-[9px] pointer-events-none"
            >
              {p.bucket.slice(5)}
            </text>
            <text
              x={p.x}
              y={p.y - 6}
              textAnchor="middle"
              className="fill-[var(--ink-text-secondary)] text-[9px] pointer-events-none"
            >
              {p.llm_called}
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}

/** 单级统计行:解析数 + LLM 调用数(全命中零 LLM 调用时带成功小勾) */
function LevelRow({
  label,
  total,
  llmCalled,
}: {
  label: string
  total: number
  llmCalled: number
}) {
  const fullHit = total > 0 && llmCalled === 0
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line py-1.5 last:border-b-0">
      <span className="flex items-center gap-1.5 text-sm text-ink">
        {label}
        {fullHit && <Check aria-hidden className="h-3.5 w-3.5 text-success" />}
      </span>
      <span className="flex items-center gap-2">
        <span className="text-xs text-ink-secondary data-text">
          {strings.dashboard.llmCallsShort} {llmCalled}
        </span>
        <Badge className="data-text">{total}</Badge>
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
  useReloadOnCategories(reload, ['parse', 'organize', 'notify', 'system'])

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
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
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
          icon={UserRoundCog}
        />
        <MetricCard
          label={strings.dashboard.pendingQueue}
          value={String(data.pending_open)}
          hint={strings.dashboard.pendingQueueUnit}
          icon={Inbox}
        />
        <MetricCard
          label={strings.dashboard.llmCallRate}
          value={llmRate === null ? '—' : formatPercent(llmRate)}
          hint={`${totalLlm} / ${totalParsed}`}
          icon={Sparkles}
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
              <Badge key={state} mark tone={episodeStateView(state as EpisodeState).tone}>
                <span className="data-text">
                  {episodeStateLabel(state)} {count}
                </span>
              </Badge>
            ))
          )}
        </div>
      </Card>
    </>
  )
}
