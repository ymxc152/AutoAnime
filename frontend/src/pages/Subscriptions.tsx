/*
 * Subscriptions —— 追番管理:列表 + 每季进度(对齐后端 SubscriptionOut)。
 * 订阅载体 = series 行 + 预生成季/集表(ARCHITECTURE §2);POST 至少一个
 * 标题,episode_count 非空时预生成 N 条 MISSING 集。RSS 地址关联走
 * 「RSS 源」页(后端 /api/rss_sources,按季挂载)。放送调度与降频随 E4。
 * 数据:GET/POST/DELETE /api/subscriptions。
 */
import { useCallback, useState } from 'react'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { strings, t } from '../strings'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageTitle,
  ProgressBar,
  Skeleton,
  StatusDot,
} from '../components'
import { seasonStateView } from '../lib/views'
import type { SubscriptionDto } from '../api/types'

const MIKAN_URL = 'https://mikanani.me'

function subscriptionTitle(sub: SubscriptionDto): string {
  return sub.title_cn ?? sub.title_romaji ?? sub.title_jp ?? `#${sub.id}`
}

function AddSubscriptionForm({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [seasonNumber, setSeasonNumber] = useState('1')
  const [episodeCount, setEpisodeCount] = useState('')
  const [fansub, setFansub] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (): Promise<void> => {
    if (title.trim() === '') {
      setError(strings.subscriptions.titleRequired)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.subscriptions.create({
        title_cn: title.trim(),
        season_number: seasonNumber === '' ? undefined : Number(seasonNumber),
        // episode_count 留空 = 只建 Series/Season,不预生成集表
        ...(episodeCount !== '' ? { episode_count: Number(episodeCount) } : {}),
        ...(fansub.trim() !== '' ? { fansub_pref: fansub.trim() } : {}),
      })
      setTitle('')
      setSeasonNumber('1')
      setEpisodeCount('')
      setFansub('')
      onDone()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.subscriptions.addFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card
      title={strings.subscriptions.addSubscription}
      description={strings.subscriptions.rssHint}
      actions={
        <a
          href={MIKAN_URL}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-medium text-primary hover:text-primary-hover"
        >
          {strings.subscriptions.mikanEntry} ↗
        </a>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label={strings.subscriptions.titleLabel} error={error} htmlFor="sub-title">
          <Input
            id="sub-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={strings.subscriptions.titlePlaceholder}
            invalid={error !== null}
            className="data-text"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={strings.subscriptions.seasonNumber} htmlFor="sub-season">
            <Input
              id="sub-season"
              inputMode="numeric"
              value={seasonNumber}
              onChange={(e) => setSeasonNumber(e.target.value.replace(/[^\d]/g, ''))}
              className="data-text"
            />
          </Field>
          <Field label={strings.subscriptions.episodeCount} htmlFor="sub-episodes">
            <Input
              id="sub-episodes"
              inputMode="numeric"
              value={episodeCount}
              onChange={(e) => setEpisodeCount(e.target.value.replace(/[^\d]/g, ''))}
              placeholder={strings.subscriptions.episodeCountPlaceholder}
              className="data-text"
            />
          </Field>
        </div>
        <Field label={strings.subscriptions.fansubPref} htmlFor="sub-fansub">
          <Input
            id="sub-fansub"
            value={fansub}
            onChange={(e) => setFansub(e.target.value)}
            placeholder={strings.subscriptions.fansubPlaceholder}
          />
        </Field>
        <div>
          <Button type="submit" variant="primary" loading={submitting}>
            {strings.subscriptions.submitAdd}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function SubscriptionRow({
  sub,
  onRemove,
  removing,
}: {
  sub: SubscriptionDto
  onRemove: (id: number) => void
  removing: boolean
}) {
  return (
    <div className="flex flex-col gap-2 border-b border-line px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{subscriptionTitle(sub)}</span>
        <Badge>{sub.media_type}</Badge>
        <Badge tone="neutral">{sub.status}</Badge>
        <Badge>{sub.fansub_pref ?? strings.subscriptions.noFansub}</Badge>
        <span className="ml-auto">
          <Button
            size="sm"
            variant="ghost"
            loading={removing}
            onClick={() => onRemove(sub.id)}
          >
            {strings.common.remove}
          </Button>
        </span>
      </div>
      {sub.seasons.length === 0 ? (
        <p className="text-xs text-ink-secondary">{strings.subscriptions.noSeasons}</p>
      ) : (
        sub.seasons.map((season) => {
          const view = seasonStateView(season.status)
          const progress =
            season.episodes_total > 0
              ? season.episodes_organized / season.episodes_total
              : 0
          return (
            <div key={season.season_id} className="flex flex-col gap-1.5">
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
                <StatusDot tone={view.tone} size={7} />
                <span className="data-text text-ink">
                  {t(strings.library.seasonN, { n: season.number })}
                </span>
                <span>{view.label}</span>
                <span className="data-text">
                  {t(strings.subscriptions.organizedOfTotal, {
                    organized: season.episodes_organized,
                    total: season.episodes_total,
                  })}
                </span>
                <span className="data-text">
                  {t(strings.subscriptions.missingCount, { count: season.episodes_missing })}
                </span>
                <span className="data-text">
                  {t(strings.subscriptions.rssCount, { count: season.rss_sources })}
                </span>
              </div>
              <ProgressBar value={progress} tone={view.tone === 'success' ? 'success' : 'primary'} />
            </div>
          )
        })
      )}
    </div>
  )
}

export function SubscriptionsPage() {
  const fetcher = useCallback(() => api.subscriptions.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)
  // 取消订阅失败不再静默(A2):复用页面级 role="alert" 错误条
  const [actionError, setActionError] = useState<string | null>(null)

  const subs = data?.items ?? []

  const remove = async (id: number): Promise<void> => {
    const sub = subs.find((s) => s.id === id)
    if (sub !== undefined && confirmId !== id) {
      setConfirmId(id)
      return
    }
    setRemovingId(id)
    setActionError(null)
    try {
      await api.subscriptions.remove(id)
      setConfirmId(null)
      reload()
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : strings.subscriptions.removeFailed)
    } finally {
      setRemovingId(null)
    }
  }

  return (
    <>
      <PageTitle title={strings.subscriptions.title} />

      {actionError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.common.actionFailed}</strong>
          {actionError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
        <Card flush>
          {error !== null ? (
            <div className="p-4">
              <ErrorState message={error} onRetry={reload} />
            </div>
          ) : loading ? (
            <div className="flex flex-col gap-2 p-4">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : subs.length === 0 ? (
            <div className="p-4">
              <EmptyState title={strings.subscriptions.empty} />
            </div>
          ) : (
            subs.map((sub) => (
              <SubscriptionRow
                key={sub.id}
                sub={sub}
                onRemove={(id) => void remove(id)}
                removing={removingId === sub.id}
              />
            ))
          )}
          {confirmId !== null && (
            <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-2.5">
              <span className="text-xs text-ink-secondary">
                {t(strings.subscriptions.removeConfirm, {
                  title: subscriptionTitle(subs.find((s) => s.id === confirmId)!),
                })}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="danger" onClick={() => void remove(confirmId)}>
                  {strings.common.confirm}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                  {strings.common.cancel}
                </Button>
              </span>
            </div>
          )}
        </Card>
        <AddSubscriptionForm onDone={reload} />
      </div>
    </>
  )
}
