/*
 * Library —— series 卡片网格 + season/episode 明细抽屉 + quality_score 徽标。
 * 数据:GET /api/series(契约假设:series 资源内嵌 seasons[].episodes[] 全树)。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Film, LibraryBig } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import { useReloadOnCategories } from '../hooks/useReloadOnEvent'
import { confirmDialog } from '../lib/confirm'
import { strings, t } from '../strings'
import {
  Badge,
  Button,
  Card,
  Drawer,
  EmptyState,
  ErrorState,
  Input,
  PageTitle,
  Pagination,
  Skeleton,
  StatusDot,
} from '../components'
import { episodeStateView, formatDate, mediaTypeLabel, qualityTone, seasonStateView } from '../lib/views'
import type { EpisodeDto, EpisodeReparseOut, SeriesDto } from '../api/types'

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
  const [loaded, setLoaded] = useState(false)
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
    <div className="relative h-24 w-16 shrink-0">
      {/* 加载占位:onLoad 前显示,加载完成后淡出 */}
      {!loaded && (
        <div
          aria-hidden
          className="absolute inset-0 flex items-center justify-center rounded-sm bg-surface-2 text-ink-muted"
        >
          <Film className="h-5 w-5" />
        </div>
      )}
      <img
        src={api.series.posterUrl(seriesId)}
        alt=""
        loading="lazy"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className={`h-24 w-16 rounded-sm border border-line object-cover transition-opacity duration-200 ${
          loaded ? 'opacity-100' : 'opacity-0'
        }`}
      />
    </div>
  )
}

function QualityBadge({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="text-xs text-ink-muted">—</span>
  }
  return <Badge tone={qualityTone(score)} mark title={strings.library.qualityScore}>{score.toFixed(1)}</Badge>
}

/** 预览展示用:取路径末段文件名(兼容 / 与 \ 分隔) */
function fileBasename(path: string | null): string {
  if (path === null) return strings.library.noFilePath
  const idx = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return idx >= 0 ? path.slice(idx + 1) : path
}

/**
 * 12-F:集行「重新识别」——两步契约的第一步(POST dry_run=true 拉预览)。
 * 预览抽屉展示「解析结果 → 将执行的动作」,确认执行(danger + confirmDialog
 * 二次确认)后才真正重命名/移动文件。409(源文件不在位等)如实 toast 展示。
 */
function EpisodeRow({
  episode,
  reparseBusy,
  reparseDisabled,
  onReparse,
}: {
  episode: EpisodeDto
  reparseBusy: boolean
  /** 预览在途时禁用所有行的「重新识别」(startReparse 对并发点击是静默 no-op,与其静默不如禁用) */
  reparseDisabled: boolean
  onReparse: (episode: EpisodeDto) => void
}) {
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
        <Button
          size="sm"
          variant="ghost"
          aria-label={`${strings.ops12f.reparseAction} ${t(strings.library.episodeN, { n: episode.number })}`}
          loading={reparseBusy}
          disabled={reparseDisabled}
          onClick={() => onReparse(episode)}
        >
          {strings.ops12f.reparseAction}
        </Button>
      </div>
    </div>
  )
}

/** 预览行:标签 + 值(data-text 等宽展示路径) */
function ReparseRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-line py-1.5 last:border-b-0">
      <span className="text-xs text-ink-secondary">{label}</span>
      <span className="data-text break-all text-sm text-ink">{value}</span>
    </div>
  )
}

function SeriesDrawer({
  series,
  onClose,
  onChanged,
}: {
  series: SeriesDto
  onClose: () => void
  onChanged: () => void
}) {
  const [seasonId, setSeasonId] = useState<number | null>(series.seasons[0]?.id ?? null)
  const season = series.seasons.find((s) => s.id === seasonId) ?? series.seasons[0]
  // 12-F:重新识别两步态(预览在途的集 id / 预览结果 / 执行中)
  const [previewingId, setPreviewingId] = useState<number | null>(null)
  const [reparsePreview, setReparsePreview] = useState<{
    episode: EpisodeDto
    preview: EpisodeReparseOut
  } | null>(null)
  const [executing, setExecuting] = useState(false)

  const startReparse = async (episode: EpisodeDto): Promise<void> => {
    if (previewingId !== null) return
    setPreviewingId(episode.id)
    try {
      const preview = await api.episodes.reparse(episode.id, { dry_run: true })
      setReparsePreview({ episode, preview })
    } catch (cause) {
      // 404/409(源文件不在位等)如实展示后端原因
      toast.error(cause instanceof Error ? cause.message : strings.common.actionFailed)
    } finally {
      setPreviewingId(null)
    }
  }

  const executeReparse = async (): Promise<void> => {
    if (reparsePreview === null) return
    // 第二步确认:danger 按钮 + confirmDialog 二次确认(会移动已归档文件)
    if (!(await confirmDialog(strings.ops12f.reparseConfirmDialog))) return
    setExecuting(true)
    try {
      const out = await api.episodes.reparse(reparsePreview.episode.id, { dry_run: false })
      toast.success(t(strings.ops12f.reparseDone, { dst: out.action.dst ?? '' }))
      setReparsePreview(null)
      onChanged()
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : strings.common.actionFailed)
    } finally {
      setExecuting(false)
    }
  }

  const preview = reparsePreview?.preview ?? null
  const skipAction = preview?.action.action === 'skip'

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
                season.episodes.map((ep) => (
                  <EpisodeRow
                    key={ep.id}
                    episode={ep}
                    reparseBusy={previewingId === ep.id}
                    reparseDisabled={previewingId !== null}
                    onReparse={(e) => void startReparse(e)}
                  />
                ))
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

      {reparsePreview !== null && preview !== null && (
        <Drawer
          open
          onClose={() => setReparsePreview(null)}
          title={strings.ops12f.reparsePreviewTitle}
          subtitle={strings.ops12f.reparsePreviewHint}
        >
          <div className="flex flex-col">
            <ReparseRow
              label={strings.ops12f.reparseOriginal}
              value={fileBasename(reparsePreview.episode.file_path)}
            />
            <ReparseRow
              label={strings.ops12f.reparseParsedTitle}
              value={
                preview.parsed === null
                  ? strings.ops12f.reparseNoParsed
                  : (preview.parsed.title ?? strings.common.unknown)
              }
            />
            <ReparseRow
              label={strings.ops12f.reparseSeasonEpisode}
              value={
                preview.parsed === null
                  ? '—'
                  : `S${String(preview.parsed.season ?? 0).padStart(2, '0')}E${String(
                      preview.parsed.episode ?? 0,
                    ).padStart(2, '0')}`
              }
            />
            <ReparseRow
              label={strings.ops12f.reparseTargetPath}
              value={preview.action.dst ?? strings.common.unknown}
            />
            {skipAction && (
              <p className="mt-2 text-xs text-warning">
                {strings.ops12f.reparseSkip}
                {typeof preview.action.reason === 'string' ? `: ${preview.action.reason}` : ''}
              </p>
            )}
          </div>
          <div className="mt-4 flex gap-2">
            {/* 危险动作:danger 语义 + confirmDialog 二次确认;守卫命中时无可执行动作 */}
            <Button
              variant="danger"
              loading={executing}
              disabled={skipAction}
              onClick={() => void executeReparse()}
            >
              {strings.ops12f.reparseConfirm}
            </Button>
            <Button variant="ghost" onClick={() => setReparsePreview(null)}>
              {strings.common.cancel}
            </Button>
          </div>
        </Drawer>
      )}
    </Drawer>
  )
}

const LIBRARY_PAGE_SIZE = 24

export function LibraryPage() {
  const [searchInput, setSearchInput] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)

  // Debounce 输入：250ms 静默后才发起后端搜索，避免请求风暴。
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(searchInput.trim())
      setPage(1)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  const fetcher = useCallback(
    () =>
      api.series.list({
        limit: LIBRARY_PAGE_SIZE,
        offset: (page - 1) * LIBRARY_PAGE_SIZE,
        ...(query === '' ? {} : { q: query }),
      }),
    [page, query],
  )
  const { data, loading, error, reload } = useApi(fetcher)
  useReloadOnCategories(reload, ['organize', 'system'])
  const [selected, setSelected] = useState<SeriesDto | null>(null)

  // 后端已完成 q 过滤与分页；前端只渲染当前页。
  const seriesList = useMemo(() => data?.items ?? [], [data])

  return (
    <>
      <PageTitle
        title={strings.library.title}
        actions={
          <Input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
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
          <div className="flex flex-col items-center py-2 text-center">
            <LibraryBig aria-hidden className="mb-3 h-10 w-10 text-ink-muted" />
            <EmptyState
              title={strings.library.empty}
              description={strings.uxfix.emptyLibraryHint}
            />
            {/* 12-UX:空态不再止步于文案,给一条去追番页的直达路径 */}
            <Link
              to="/subscriptions"
              className="mt-2 inline-flex h-7 w-fit items-center gap-1.5 rounded-sm px-2 text-xs font-medium text-ink-secondary transition-colors duration-[var(--ink-transition-fast)] hover:bg-surface-2 hover:text-ink"
            >
              {strings.uxfix.emptyRssCta}
            </Link>
          </div>
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
                className="flex gap-3 rounded-md border border-line bg-surface p-3 text-left shadow-soft-sm transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-soft-md"
              >
                <SeriesPoster seriesId={series.id} title={seriesTitle(series)} />
                <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="line-clamp-1 font-medium text-ink" title={seriesTitle(series)}>{seriesTitle(series)}</p>
                  <Badge>{mediaTypeLabel(series.media_type)}</Badge>
                </div>
                {series.title_romaji !== null && (
                  <p className="mt-0.5 line-clamp-1 text-xs text-ink-muted" title={series.title_romaji}>{series.title_romaji}</p>
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
                    {t(strings.subscriptions.organizedOfTotal, {
                      organized: String(stats.organized),
                      total: String(stats.total),
                    })}
                  </span>
                  <QualityBadge score={stats.avgQuality === null ? null : Math.round(stats.avgQuality * 10) / 10} />
                </div>
                </div>
              </button>
            )
          })}
        </div>
      )}

      {data !== null && data.total > LIBRARY_PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={LIBRARY_PAGE_SIZE}
          total={data.total}
          onPageChange={setPage}
        />
      )}

      {data !== null && (
        <p className="text-xs text-ink-secondary data-text">
          {t(strings.common.total, { count: data.total })}
        </p>
      )}

      {selected !== null && (
        <SeriesDrawer series={selected} onClose={() => setSelected(null)} onChanged={reload} />
      )}
    </>
  )
}


