/*
 * RSSSources —— 源管理:增删启停。
 * 数据:GET/POST/PATCH/DELETE /api/rss_sources。
 */
import { useCallback, useMemo, useState } from 'react'
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
  Select,
  StatusDot,
  Switch,
  type Column,
} from '../components'
import { formatDateTime } from '../lib/views'
import type { RssSourceDto, SubscriptionDto } from '../api/types'

/** 下拉选项:番名 + 季号 + season id 拼显示文案(B2:手输主键全 UI 无处可查) */
interface SeasonOption {
  id: number
  label: string
}

function subscriptionTitle(sub: SubscriptionDto): string {
  return sub.title_cn ?? sub.title_romaji ?? sub.title_jp ?? `#${sub.id}`
}

/** 订阅列表 → 季下拉选项(按 series→seasons 展开,不新增后端端点) */
function buildSeasonOptions(subs: SubscriptionDto[]): SeasonOption[] {
  return subs.flatMap((sub) =>
    sub.seasons.map((season) => ({
      id: season.season_id,
      label: t(strings.rssSources.seasonOption, {
        title: subscriptionTitle(sub),
        n: season.number,
        id: season.season_id,
      }),
    })),
  )
}

function AddSourceForm({
  onDone,
  seasonOptions,
}: {
  onDone: () => void
  seasonOptions: SeasonOption[]
}) {
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
    // 后端 RssSourceCreateIn:season_id 必填(外键指向 season.id)
    if (seasonId === '') {
      setError(strings.rssSources.seasonRequired)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.rssSources.create({
        url: url.trim(),
        season_id: Number(seasonId),
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
        <Field
          label={strings.rssSources.season}
          description={strings.rssSources.seasonHint}
          htmlFor="rss-season"
        >
          <Select
            id="rss-season"
            value={seasonId}
            onChange={(e) => setSeasonId(e.target.value)}
          >
            <option value="">
              {seasonOptions.length === 0
                ? strings.rssSources.seasonEmptyOption
                : strings.rssSources.seasonPlaceholder}
            </option>
            {seasonOptions.map((option) => (
              <option key={option.id} value={String(option.id)}>
                {option.label}
              </option>
            ))}
          </Select>
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
  // 季下拉数据源:GET /api/subscriptions(后端 SubscriptionOut 内嵌 seasons)
  const subsFetcher = useCallback(() => api.subscriptions.list({ limit: 200 }), [])
  const { data: subsData } = useApi(subsFetcher)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  // 启停/移除失败不再静默(A2):复用页面级 role="alert" 错误条
  const [actionError, setActionError] = useState<string | null>(null)

  const rows = data?.items ?? []
  const seasonOptions = useMemo(() => buildSeasonOptions(subsData?.items ?? []), [subsData])

  const toggle = async (source: RssSourceDto): Promise<void> => {
    setBusyId(source.id)
    setActionError(null)
    try {
      await api.rssSources.update(source.id, { enabled: !source.enabled })
      reload()
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : strings.rssSources.toggleFailed)
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
    setActionError(null)
    try {
      await api.rssSources.remove(id)
      setConfirmId(null)
      reload()
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : strings.rssSources.removeFailed)
    } finally {
      setBusyId(null)
    }
  }

  const columns: Column<RssSourceDto>[] = [
    {
      key: 'url',
      header: strings.rssSources.url,
      // 约定:URL 中内嵌的 token 随 URL 明文展示;独立 token 字段才按密钥处理。
      sticky: true,
      render: (row) => (
        <span className="data-text block max-w-md truncate text-sm text-ink" title={row.url}>
          {row.url}
        </span>
      ),
    },
    {
      key: 'season',
      header: strings.rssSources.season,
      // 能解析到订阅季则显示番名+季号;解析不到的旧数据回显原 season id
      render: (row) => {
        const match = seasonOptions.find((option) => option.id === row.season_id)
        return (
          <span className="data-text text-sm text-ink">
            {match !== undefined ? match.label : row.season_id}
          </span>
        )
      },
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

      {actionError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.common.actionFailed}</strong>
          {actionError}
        </div>
      )}

      <AddSourceForm onDone={reload} seasonOptions={seasonOptions} />
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
