/*
 * Dashboard —— 指标卡(人工介入率/待确认队列/LLM 调用率)+ 三级统计 + LLM 周曲线 + 库内集状态。
 * 数据:GET /api/metrics(对齐后端 MetricsOut:intervention_rate/by_level/
 * llm_call_curve_weekly/pending_open/episode_states)。
 */
import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  ChartLine,
  Check,
  FolderDown,
  Inbox,
  LibraryBig,
  Sparkles,
  Tv,
  UserRoundCog,
  type LucideIcon,
} from 'lucide-react'
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
  const height = 72
  // 顶部留白:峰值点的数值标签(y = 点位-6,9px 字形上探约 7px)不得越过 svg 上沿,
  // 否则等比放大后字形压进卡片标题行(实测重叠 50%)
  const topPad = 14
  // viewBox 宽度下限 280 与 min-w 匹配,避免数据点少时 SVG 被等比放大成巨图(实测修复)
  const width = Math.max(active.length * 44, 280)
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
    x: ((i + 0.5) * width) / active.length,
    y: topPad + (1 - p.llm_called / max) * (height - topPad - 4),
  }))
  const last = pts.at(-1)
  if (last === undefined) {
    return null
  }
  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height + 18}`}
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
              x={p.x - width / active.length / 2}
              y={0}
              width={width / active.length}
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

/**
 * 12-UX:新手三步引导卡 —— 订阅数为 0(首次使用)时顶部展示,
 * 三行均可点击直达对应页面;一旦有订阅(total > 0)整卡消失,纯静态指引不记状态。
 */
function OnboardingCard() {
  const fetcher = useCallback(() => api.subscriptions.list({ limit: 1 }), [])
  const { data } = useApi(fetcher)

  if (data === null || data.total > 0) {
    return null
  }

  // 12-IA:三步文案与跳转对新流程(选番 → 导入 → 媒体库),动作链接与文案指向一致;
  // P1-UX:步骤②补前置说明(qBittorrent 需先配置)
  const steps: { icon: LucideIcon; label: string; note?: string; to: string }[] = [
    { icon: Tv, label: strings.uxfix.onboardingStep1, to: '/subscriptions' },
    {
      icon: FolderDown,
      label: strings.uxfix.onboardingStep2,
      note: strings.uxfix.onboardingStep2Note,
      to: '/pipeline',
    },
    { icon: LibraryBig, label: strings.uxfix.onboardingStep3, to: '/library' },
  ]

  return (
    <Card title={strings.uxfix.onboardingTitle} flush className="mb-3">
      <nav className="flex flex-col px-2 pb-2">
        {steps.map(({ icon: Icon, label, note, to }) => (
          <Link
            key={to}
            to={to}
            className="flex items-center gap-2.5 rounded-sm px-2 py-2 text-sm text-ink-secondary transition-colors duration-[var(--ink-transition-fast)] hover:bg-surface-2 hover:text-ink"
          >
            <span
              aria-hidden
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-ink-muted"
            >
              <Icon className="h-3.5 w-3.5" />
            </span>
            <span className="flex min-w-0 flex-col">
              <span>{label}</span>
              {note !== undefined && (
                <span className="text-xs text-ink-muted">{note}</span>
              )}
            </span>
          </Link>
        ))}
      </nav>
    </Card>
  )
}

/**
 * 12-F:「识别指标」区块 —— GET /api/report(CLI report --json 同构)。
 * 与上方指标卡互补:这里放「累计学习成效」视角(总解析/LLM 兜底/
 * 归档事件/人工纠正),不与按周期聚合的三级管线统计重复。
 */
function ReportCard() {
  const fetcher = useCallback(() => api.report.get(), [])
  const { data, loading, error, reload } = useApi(fetcher)
  useReloadOnCategories(reload, ['parse', 'system'])

  const items: { label: string; value: string }[] =
    data === null
      ? []
      : [
          {
            label: strings.ops12f.reportParsed,
            value: String(data.parse_events.total),
          },
          {
            label: strings.ops12f.reportLlmFallback,
            value: String(data.parse_events.llm_called_total),
          },
          {
            label: strings.ops12f.reportLlmRate,
            value: formatPercent(data.parse_events.llm_call_rate),
          },
          {
            label: strings.ops12f.reportArchived,
            value: String(data.manual_intervention_rate.archived_events),
          },
          {
            label: strings.ops12f.reportManual,
            value: String(data.manual_intervention_rate.manual_correction_events),
          },
          {
            label: strings.ops12f.reportInterventionRate,
            value:
              data.manual_intervention_rate.rate === null
                ? '—'
                : formatPercent(data.manual_intervention_rate.rate),
          },
        ]

  return (
    <Card title={strings.ops12f.reportTitle} description={strings.ops12f.reportHint} flush>
      <div className="px-4 pb-3">
        {error !== null ? (
          <p role="alert" className="py-2 text-sm text-danger">
            {strings.ops12f.reportLoadFailed}: {error}
          </p>
        ) : loading || data === null ? (
          <Skeleton className="h-16" />
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {items.map((item) => (
              <div key={item.label} className="flex flex-col gap-0.5">
                <span className="text-xs text-ink-secondary">{item.label}</span>
                <span className="data-text text-xl font-semibold tracking-tight text-ink">
                  {item.value}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  )
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
      {/* 12-UX:首次使用(订阅数 0)时的新手三步引导卡,位于指标卡之上 */}
      <OnboardingCard />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {/* 12-UX 双口径去歧义:本卡为「周期人工介入率」(label 已改名),hint 注明统计范围,
            与下方「识别指标」的累计口径「人工介入率」区分(strings 冻结,口径文案用字面量) */}
        <MetricCard
          label={strings.dashboard.manualInterventionRate}
          value={data.intervention_rate === null ? '—' : formatPercent(data.intervention_rate)}
          hint={`近几个审计周期 · ${strings.dashboard.auditManual} ${data.audit_manual} / ${strings.dashboard.auditTotal} ${data.audit_total}`}
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

      {/* 12-F:识别指标(GET /api/report,累计视角) */}
      <ReportCard />

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
