/*
 * Library —— series 卡片网格 + season/episode 明细抽屉 + quality_score 徽标。
 * 数据:GET /api/series(契约假设:series 资源内嵌 seasons[].episodes[] 全树)。
 */
import { useCallback, useMemo, useState } from 'react'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import { strings, t } from '../strings'
import {
  Badge,
  Card,
  Drawer,
  EmptyState,
  ErrorState,
  Input,
  PageTitle,
  Skeleton,
  StatusDot,
} from '../components'
import { episodeStateView, formatDate, mediaTypeLabel, qualityTone, seasonStateView } from '../lib/views'
import type { EpisodeDto, SeriesDto } from '../api/types'

function seriesTitle(series: SeriesDto): string {
  return series.title_cn ?? series.title_romaji ?? series.title_jp ?? `#${series.id}`
}

/** 聚合统计:各状态集数 + 平均质量分 */
function seriesStats(series: SeriesDto): {
  total: number
  organized: number
  missing: number
  avgQuality: number | null
} {
  const episodes = series.seasons.flatMap((s) => s.episodes)
  const scored = episodes.filter((e) => e.quality_score !== null)
  return {
    total: episodes.length,
    organized: episodes.filter((e) => e.state === 'organized' || e.state === 'upgraded').length,
    missing: episodes.filter((e) => e.state === 'missing').length,
    avgQuality:
      scored.length > 0
        ? scored.reduce((sum, e) => sum + (e.quality_score ?? 0), 0) / scored.length
        : null,
  }
}

/**
 * 海报:本地库 poster 优先(后端代理)。<img> 无法携带 X-API-Token 头,后端开启
 * token 认证时此端点会 401;无论 404(无海报)还是 401(未授权),浏览器对
 * <img> 的非成功响应都触发 onError → 统一降级为首字占位块,不额外弹提示
 * (README 已知边界有记录,见 A3 审查项)。
 */
function SeriesPoster({ seriesId, title }: { seriesId: number; title: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div
        aria-hidden
        className="flex h-24 w-16 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-lg font-medium text-ink-secondary"
      >
        {title.slice(0, 1)}
      </div>
    )
  }
  return (
    <img
      src={api.series.posterUrl(seriesId)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-24 w-16 shrink-0 rounded-sm border border-line object-cover"
    />
  )
}

function QualityBadge({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="text-xs text-ink-muted">—</span>
  }
  return <Badge tone={qualityTone(score)} mark title={strings.library.qualityScore}>{score.toFixed(1)}</Badge>
}

function EpisodeRow({ episode }: { episode: EpisodeDto }) {
  const view = episodeStateView(episode.state)
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-2 last:border-b-0">
      <div className="flex min-w-0 items-center gap-2">
        <StatusDot tone={view.tone} />
        <span className="data-text shrink-0 text-sm text-ink">
          {t(strings.library.episodeShort, { n: String(episode.number).padStart(2, '0') })}
        </span>
        <span className="text-xs text-ink-secondary">{view.label}</span>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span className="data-text text-xs text-ink-secondary">{formatDate(episode.air_date)}</span>
        <QualityBadge score={episode.quality_score} />
      </div>
    </div>
  )
}

function SeriesDrawer({ series, onClose }: { series: SeriesDto; onClose: () => void }) {
  const [seasonId, setSeasonId] = useState<number | null>(series.seasons[0]?.id ?? null)
  const season = series.seasons.find((s) => s.id === seasonId) ?? series.seasons[0]

  return (
    <Drawer
      open
      onClose={onClose}
      title={seriesTitle(series)}
      subtitle={`${mediaTypeLabel(series.media_type)} · ${series.title_romaji ?? series.title_jp ?? ''}`}
    >
      {season === undefined ? (
        <EmptyState title={strings.common.empty} />
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-1.5">
            {series.seasons.map((s) => {
              const view = seasonStateView(s.status)
              const active = s.id === season?.id
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSeasonId(s.id)}
                  className={`inline-flex items-center gap-1.5 rounded-sm border px-2 py-1 text-xs transition-colors ${
                    active
                      ? 'border-primary bg-primary-light text-ink'
                      : 'border-line text-ink-secondary hover:bg-surface-2'
                  }`}
                >
                  <StatusDot tone={view.tone} size={7} />
                  {t(strings.library.seasonN, { n: s.number })}
                  <span className="data-text">{s.episodes.length}</span>
                </button>
              )
            })}
          </div>
          {season !== undefined && (
            <div>
              {season.episodes.length === 0 ? (
                <EmptyState title={strings.common.empty} />
              ) : (
                season.episodes.map((ep) => <EpisodeRow key={ep.id} episode={ep} />)
              )}
            </div>
          )}
          {season !== undefined && (
            <div className="flex flex-col gap-1 border-t border-line pt-2 text-xs text-ink-secondary">
              <span>
                {strings.library.qualityScore}:{' '}
                {season.episodes.some((e) => e.quality_score !== null)
                  ? season.episodes
                      .filter((e) => e.quality_score !== null)
                      .map((e) => e.quality_score!.toFixed(1))
                      .join(' / ')
                  : '—'}
              </span>
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}

export function LibraryPage() {
  const [query, setQuery] = useState('')
  // 后端 GET /api/series 不支持标题过滤:一次拉全量(后端统一分页上限 200),搜索在前端做
  const fetcher = useCallback(() => api.series.list({ limit: 200 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [selected, setSelected] = useState<SeriesDto | null>(null)

  const seriesList = useMemo(() => {
    const all = data?.items ?? []
    if (query === '') return all
    const needle = query.toLowerCase()
    return all.filter(
      (series) =>
        (series.title_cn ?? '').toLowerCase().includes(needle) ||
        (series.title_jp ?? '').toLowerCase().includes(needle) ||
        (series.title_romaji ?? '').toLowerCase().includes(needle),
    )
  }, [data, query])

  return (
    <>
      <PageTitle
        title={strings.library.title}
        actions={
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={strings.library.searchPlaceholder}
            aria-label={strings.library.searchPlaceholder}
            className="w-56"
          />
        }
      />

      {error !== null ? (
        <ErrorState message={error} onRetry={reload} />
      ) : loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : seriesList.length === 0 ? (
        <Card>
          <EmptyState
            title={strings.library.empty}
            description={strings.subscriptions.mikanHint}
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {seriesList.map((series) => {
            const stats = seriesStats(series)
            return (
              <button
                key={series.id}
                type="button"
                onClick={() => setSelected(series)}
                className="flex gap-3 rounded-md border border-line bg-surface p-3 text-left shadow-soft-sm transition-shadow hover:shadow-soft-md"
              >
                <SeriesPoster seriesId={series.id} title={seriesTitle(series)} />
                <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-ink">{seriesTitle(series)}</p>
                  <Badge>{mediaTypeLabel(series.media_type)}</Badge>
                </div>
                {series.title_romaji !== null && (
                  <p className="mt-0.5 text-xs text-ink-secondary">{series.title_romaji}</p>
                )}
                <div className="mt-2 flex items-center gap-2 text-xs text-ink-secondary">
                  <span className="data-text">
                    {series.seasons.length} {strings.library.seasons} · {stats.total} {strings.library.episodes}
                  </span>
                  {stats.missing > 0 && (
                    <Badge tone="danger" mark>
                      {strings.library.state.missing} {stats.missing}
                    </Badge>
                  )}
                </div>
                <div className="mt-2 flex items-center justify-between">
                  <span className="data-text text-sm text-ink">
                    {stats.organized}/{stats.total}
                  </span>
                  <QualityBadge score={stats.avgQuality === null ? null : Math.round(stats.avgQuality * 10) / 10} />
                </div>
                </div>
              </button>
            )
          })}
        </div>
      )}

      {data !== null && (
        <p className="text-xs text-ink-secondary data-text">
          {t(strings.common.total, { count: data.total })}
        </p>
      )}

      {selected !== null && <SeriesDrawer series={selected} onClose={() => setSelected(null)} />}
    </>
  )
}


