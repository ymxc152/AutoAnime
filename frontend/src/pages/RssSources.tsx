/*
 * RSSSources —— 源管理:增删启停。
 * 数据:GET/POST/PATCH/DELETE /api/rss_sources。
 */
import { useCallback, useState } from 'react'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { strings, t } from '../strings'
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageTitle,
  StatusDot,
  Switch,
  type Column,
} from '../components'
import { formatDateTime } from '../lib/views'
import type { RssSourceDto } from '../api/types'

function AddSourceForm({ onDone }: { onDone: () => void }) {
  const [url, setUrl] = useState('')
  const [seasonId, setSeasonId] = useState('')
  const [token, setToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (): Promise<void> => {
    if (url.trim() === '') {
      setError(strings.rssSources.urlRequired)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.rssSources.create({
        url: url.trim(),
        season_id: seasonId === '' ? undefined : Number(seasonId),
        token: token === '' ? undefined : token,
      })
      setUrl('')
      setSeasonId('')
      setToken('')
      onDone()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card title={strings.rssSources.addSource} className="mb-4">
      <form
        className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr_1fr_auto] md:items-start"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label={strings.rssSources.url} error={error} htmlFor="rss-url">
          <Input
            id="rss-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            invalid={error !== null}
            className="data-text"
          />
        </Field>
        <Field label={strings.rssSources.season} htmlFor="rss-season">
          <Input
            id="rss-season"
            value={seasonId}
            onChange={(e) => setSeasonId(e.target.value.replace(/\D/g, ''))}
            inputMode="numeric"
          />
        </Field>
        <Field label={strings.rssSources.token} description={strings.rssSources.tokenHint} htmlFor="rss-token">
          <Input
            id="rss-token"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </Field>
        <div className="md:pt-6">
          <Button type="submit" variant="primary" loading={submitting}>
            {strings.rssSources.addSubmit}
          </Button>
        </div>
      </form>
    </Card>
  )
}

export function RssSourcesPage() {
  const fetcher = useCallback(() => api.rssSources.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const rows = data?.items ?? []

  const toggle = async (source: RssSourceDto): Promise<void> => {
    setBusyId(source.id)
    try {
      await api.rssSources.update(source.id, { enabled: !source.enabled })
      reload()
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (id: number): Promise<void> => {
    if (confirmId !== id) {
      setConfirmId(id)
      return
    }
    setBusyId(id)
    try {
      await api.rssSources.remove(id)
      setConfirmId(null)
      reload()
    } finally {
      setBusyId(null)
    }
  }

  const columns: Column<RssSourceDto>[] = [
    {
      key: 'url',
      header: strings.rssSources.url,
      render: (row) => (
        <span className="data-text block max-w-md truncate text-sm text-ink" title={row.url}>
          {row.url}
        </span>
      ),
    },
    {
      key: 'season',
      header: strings.rssSources.season,
      render: (row) => (
        <span className="data-text text-sm text-ink">{row.season_id ?? '—'}</span>
      ),
    },
    {
      key: 'token',
      header: strings.rssSources.token,
      render: (row) => (
        <Badge tone={row.has_token ? 'success' : 'neutral'} mark>
          {row.has_token ? '已配置' : '无'}
        </Badge>
      ),
    },
    {
      key: 'lastPolled',
      header: strings.rssSources.lastPolledAt,
      render: (row) => (
        <span className="data-text text-xs text-ink-secondary">
          {formatDateTime(row.last_polled_at)}
        </span>
      ),
    },
    {
      key: 'enabled',
      header: strings.common.enable,
      render: (row) => (
        <span className="flex items-center gap-2">
          <StatusDot tone={row.enabled ? 'success' : 'neutral'} size={7} />
          <Switch
            checked={row.enabled}
            disabled={busyId === row.id}
            onChange={() => void toggle(row)}
            aria-label={`${strings.common.enable} ${row.url}`}
          />
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (row) =>
        confirmId === row.id ? (
          <span className="flex items-center gap-1.5">
            <Button size="sm" variant="danger" loading={busyId === row.id} onClick={() => void remove(row.id)}>
              {strings.common.confirm}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
              {strings.common.cancel}
            </Button>
          </span>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => void remove(row.id)}>
            {strings.common.remove}
          </Button>
        ),
    },
  ]

  return (
    <>
      <PageTitle title={strings.rssSources.title} />
      <AddSourceForm onDone={reload} />
      <Card flush>
        {error !== null ? (
          <div className="p-4">
            <ErrorState message={error} onRetry={reload} />
          </div>
        ) : (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(row) => row.id}
            loading={loading}
            empty={<EmptyState title={strings.rssSources.empty} />}
            footer={
              <span className="text-xs text-ink-secondary data-text">
                {t(strings.common.total, { count: data?.total ?? 0 })}
              </span>
            }
          />
        )}
      </Card>
    </>
  )
}
