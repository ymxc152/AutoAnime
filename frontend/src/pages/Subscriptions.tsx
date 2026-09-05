/*
 * Subscriptions —— 追番管理:列表 + 放送进度条 + 降频状态标;
 * Mikan 选番入口(提示文案:每番只订一个字幕组)。
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
import { formatDate } from '../lib/views'
import type { SubscriptionDto } from '../api/types'

const MIKAN_URL = 'https://mikanani.me'

function seasonStateTone(state: SubscriptionDto['state']): 'success' | 'info' | 'neutral' {
  if (state === 'collected') return 'success'
  if (state === 'airing') return 'info'
  return 'neutral'
}

function AddSubscriptionForm({ onDone }: { onDone: () => void }) {
  const [url, setUrl] = useState('')
  const [fansub, setFansub] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (): Promise<void> => {
    if (url.trim() === '') {
      setError(strings.subscriptions.urlRequired)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.subscriptions.create({ rss_url: url.trim(), fansub: fansub.trim() || undefined })
      setUrl('')
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
      description={strings.subscriptions.mikanHint}
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
        <Field label={strings.subscriptions.rssUrl} error={error} htmlFor="sub-rss-url">
          <Input
            id="sub-rss-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={strings.subscriptions.rssUrlPlaceholder}
            invalid={error !== null}
            className="data-text"
          />
        </Field>
        <Field label={strings.subscriptions.fansubPref} htmlFor="sub-fansub">
          <Input
            id="sub-fansub"
            value={fansub}
            onChange={(e) => setFansub(e.target.value)}
            placeholder={strings.subscriptions.mikanHint}
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
  const tone = seasonStateTone(sub.state)
  const progress =
    sub.episodes_total > 0
      ? sub.episodes_collected / sub.episodes_total
      : 0
  return (
    <div className="flex flex-col gap-2 border-b border-line px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <StatusDot tone={tone} />
        <span className="font-medium text-ink">{sub.title}</span>
        <span className="data-text text-xs text-ink-secondary">
          S{String(sub.season_number).padStart(2, '0')}
        </span>
        <Badge>
          {sub.fansub_pref ?? strings.subscriptions.noFansub}
        </Badge>
        {sub.reduced_frequency && (
          <Badge tone="warning" mark title={strings.subscriptions.reducedFrequencyHint}>
            {strings.subscriptions.reducedFrequency}
          </Badge>
        )}
        {!sub.enabled && <Badge tone="neutral">{strings.common.disabled}</Badge>}
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
      <ProgressBar value={progress} tone={sub.state === 'collected' ? 'success' : 'primary'} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-secondary">
        <span className="data-text">
          {t(strings.subscriptions.collectedOfAired, {
            collected: sub.episodes_collected,
            aired: sub.episodes_aired,
            total: sub.episodes_total,
          })}
        </span>
        {sub.next_air_date !== null && (
          <span className="data-text">
            {strings.subscriptions.nextAirDate}: {formatDate(sub.next_air_date)}
          </span>
        )}
      </div>
    </div>
  )
}

export function SubscriptionsPage() {
  const fetcher = useCallback(() => api.subscriptions.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)

  const subs = data?.items ?? []

  const remove = async (id: number): Promise<void> => {
    const sub = subs.find((s) => s.id === id)
    if (sub !== undefined && confirmId !== id) {
      setConfirmId(id)
      return
    }
    setRemovingId(id)
    try {
      await api.subscriptions.remove(id)
      setConfirmId(null)
      reload()
    } finally {
      setRemovingId(null)
    }
  }

  return (
    <>
      <PageTitle title={strings.subscriptions.title} />
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
                  title: subs.find((s) => s.id === confirmId)?.title ?? '',
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
