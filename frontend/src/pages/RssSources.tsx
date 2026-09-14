/*
 * RSSSources —— 源管理:增删启停。
 * 数据:GET/POST/PATCH/DELETE /api/rss_sources。
 * 12-IA 弹窗化:「添加源」常驻表单卡 → 标题行按钮 + 居中 Dialog(编辑仍走右侧 Drawer)。
 */
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Info, RefreshCw, Rss } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { useReloadOnMessages } from '../hooks/useReloadOnEvent'
import { strings, t } from '../strings'
import {
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
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
import { Checkbox } from '@/components/ui/checkbox'
// 12-IA 弹窗化:添加源常驻表单卡 → 按钮 + 居中 Dialog(编辑仍走右侧 Drawer)
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { RssSourceDto, RssSourceUpdateBody, SubscriptionDto } from '../api/types'

/** 下拉选项:番名 + 季号 + season id 拼显示文案(B2:手输主键全 UI 无处可查) */
interface SeasonOption {
  id: number
  label: string
}

function subscriptionTitle(sub: SubscriptionDto): string {
  return sub.title_cn ?? sub.title_romaji ?? sub.title_jp ?? `#${sub.id}`
}

/** URL → host;解析失败/无 host 时回退原样(仅展示用,不改 row.url 数据) */
function urlHost(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
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

/**
 * 添加源弹窗(12-IA 弹窗化:原常驻 AddSourceForm 表单卡整体迁入,
 * url/关联季/令牌逻辑与无可选季「去追番」引导不变,仅容器从卡片变居中 Dialog;
 * 提交成功后关闭并刷新)。
 */
function AddSourceDialog({
  open,
  onOpenChange,
  onDone,
  seasonOptions,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
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
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-1.5">
            <Rss aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
            {strings.rssSources.addSource}
          </DialogTitle>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
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
              <option value="" className="text-ink-muted">
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
            {/* 12-UX:无可选季时不再让用户自己找侧栏,给一条去追番页的直达路径 */}
            {seasonOptions.length === 0 && (
              <Link
                to="/subscriptions"
                className="inline-flex h-7 w-fit items-center gap-1.5 self-start rounded-sm px-2 text-xs font-medium text-ink-secondary transition-colors duration-[var(--ink-transition-fast)] hover:bg-surface-2 hover:text-ink"
              >
                {strings.uxfix.emptyRssCta}
              </Link>
            )}
          </Field>
          <Field label={strings.rssSources.token} description={strings.rssSources.tokenHint} htmlFor="rss-token">
            <Input
              id="rss-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </Field>
          <div>
            <Button type="submit" variant="primary" loading={submitting}>
              {strings.rssSources.addSubmit}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 添加聚合源弹窗(批次三):一个 feed 混多部番的通用源,不绑季;
 * 轮询时对全部活跃订阅逐条对齐,命中才进下载链路。
 * URL/令牌 + 全局 include/exclude 关键词规则(先于 series 级规则)。
 */
function AddAggregateDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [includeKeywords, setIncludeKeywords] = useState('')
  const [excludeKeywords, setExcludeKeywords] = useState('')
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
        kind: 'aggregate',
        token: token === '' ? undefined : token,
        include_keywords: includeKeywords.trim() === '' ? undefined : includeKeywords.trim(),
        exclude_keywords: excludeKeywords.trim() === '' ? undefined : excludeKeywords.trim(),
      })
      setUrl('')
      setToken('')
      setIncludeKeywords('')
      setExcludeKeywords('')
      onDone()
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-1.5">
            <Rss aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
            {strings.rssSources.aggregateTitle}
          </DialogTitle>
        </DialogHeader>
        <p className="text-xs text-ink-secondary">{strings.rssSources.aggregateHint}</p>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <Field label={strings.rssSources.url} error={error} htmlFor="rss-agg-url">
            <Input
              id="rss-agg-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              invalid={error !== null}
              className="data-text"
            />
          </Field>
          <Field label={strings.rssSources.token} description={strings.rssSources.tokenHint} htmlFor="rss-agg-token">
            <Input
              id="rss-agg-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </Field>
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium text-ink">{strings.rssSources.globalRules}</p>
            <p className="text-xs text-ink-secondary">{strings.rssSources.keywordsHint}</p>
          </div>
          <Field label={strings.rssSources.includeKeywords} htmlFor="rss-agg-include">
            <Input
              id="rss-agg-include"
              value={includeKeywords}
              onChange={(e) => setIncludeKeywords(e.target.value)}
              className="data-text"
            />
          </Field>
          <Field label={strings.rssSources.excludeKeywords} htmlFor="rss-agg-exclude">
            <Input
              id="rss-agg-exclude"
              value={excludeKeywords}
              onChange={(e) => setExcludeKeywords(e.target.value)}
              className="data-text"
            />
          </Field>
          <div>
            <Button type="submit" variant="primary" loading={submitting}>
              {strings.rssSources.addSubmit}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditSourceDrawer({
  source,
  onDone,
  onClose,
}: {
  source: RssSourceDto
  onDone: () => void
  onClose: () => void
}) {
  const [url, setUrl] = useState(source.url)
  const [token, setToken] = useState('')
  const [clearToken, setClearToken] = useState(false)
  const [enabled, setEnabled] = useState(source.enabled)
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
      // token 语义：undefined = 不修改；null = 清除；字符串 = 更新。
      const body: RssSourceUpdateBody = { url: url.trim(), enabled }
      if (clearToken) {
        body.token = null
      } else if (token !== '') {
        body.token = token
      }
      await api.rssSources.update(source.id, body)
      onDone()
      onClose()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Drawer open onClose={onClose} title={strings.rssSources.editTitle} subtitle={source.url}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label={strings.rssSources.url} htmlFor="rss-edit-url">
          <Input
            id="rss-edit-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            invalid={error !== null}
            className="data-text"
          />
        </Field>
        <Field
          label={strings.rssSources.token}
          description={clearToken ? strings.rssSources.tokenHint : strings.rssSources.tokenUnchangedHint}
          htmlFor="rss-edit-token"
        >
          <Input
            id="rss-edit-token"
            type="password"
            value={clearToken ? '' : token}
            disabled={clearToken}
            onChange={(e) => setToken(e.target.value)}
          />
        </Field>
        {source.has_token && (
          <label className="flex items-center gap-2 text-sm text-ink">
            <Checkbox
              checked={clearToken}
              onCheckedChange={(checked) => setClearToken(checked === true)}
              aria-label={strings.rssSources.clearToken}
              className="h-3.5 w-3.5"
            />
            {strings.rssSources.clearToken}
          </label>
        )}
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-ink">{strings.common.enable}</span>
          <Switch checked={enabled} onChange={setEnabled} aria-label={`${strings.common.enable} edit`} />
        </div>
        {error !== null && (
          <p role="alert" className="text-sm font-medium text-danger">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" loading={submitting}>
            {strings.common.save}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            {strings.common.cancel}
          </Button>
        </div>
      </form>
    </Drawer>
  )
}

export function RssSourcesPage() {
  const fetcher = useCallback(() => api.rssSources.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  useReloadOnMessages(reload, [
    'rss_source.created',
    'rss_source.updated',
    'rss_source.deleted',
    // 12-F:立即轮询完成后后端广播 rss_source.polled,列表对齐 last_polled_at
    'rss_source.polled',
  ])
  // 季下拉数据源:GET /api/subscriptions(后端 SubscriptionOut 内嵌 seasons)
  const subsFetcher = useCallback(() => api.subscriptions.list({ limit: 200 }), [])
  const { data: subsData } = useApi(subsFetcher)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  // 12-F:行内「立即轮询」进行中的源 id(null = 空闲;防重复点击)
  const [pollingId, setPollingId] = useState<number | null>(null)
  const [editingSource, setEditingSource] = useState<RssSourceDto | null>(null)
  // 12-IA 弹窗化:添加源弹窗开关
  const [adding, setAdding] = useState(false)
  // 批次三:添加聚合源弹窗开关(一个 feed 混多部番,不绑季)
  const [addingAggregate, setAddingAggregate] = useState(false)
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

  // 12-F:行内立即轮询(POST /api/rss_sources/{id}/poll),按响应字段组织反馈:
  // fetch_error 如实透出;409 区分「源停用」与「并发轮询」两种原因。
  const poll = async (source: RssSourceDto): Promise<void> => {
    if (pollingId !== null) return
    setPollingId(source.id)
    try {
      const result = await api.rssSources.poll(source.id)
      if (result.fetch_error !== null) {
        toast.warning(`${strings.ops12f.pollFetchError}: ${result.fetch_error}`)
      } else if (result.skipped_not_due) {
        toast.info(strings.ops12f.pollSkipped)
      } else {
        toast.success(
          t(strings.ops12f.pollDone, {
            picked: result.picked,
            completed: result.download.completed,
          }),
        )
      }
      reload()
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        toast.warning(
          cause.message.includes('disabled') ? strings.ops12f.pollDisabled : strings.ops12f.pollConflict,
        )
      } else {
        toast.error(cause instanceof ApiError ? cause.message : strings.ops12f.pollFailed)
      }
    } finally {
      setPollingId(null)
    }
  }

  const columns: Column<RssSourceDto>[] = [
    {
      key: 'url',
      header: strings.rssSources.url,
      // 约定:URL 中内嵌的 token 随 URL 明文展示;独立 token 字段才按密钥处理。
      sticky: true,
      // 12-C:域名主体加粗、后缀路径弱化;解析失败回退原样。完整 URL 走 title 悬停。
      render: (row) => {
        const host = urlHost(row.url)
        // 12-C:在完整 URL 中定位 host 后取其后缀路径(裸 host 前缀匹配对绝对 URL 恒空,已修);
        // 解析失败回退原样。完整 URL 走 title 悬停。
        const idx = row.url.indexOf(host)
        const rest = idx >= 0 && idx + host.length < row.url.length
          ? row.url.slice(idx + host.length)
          : ''
        return (
          // 12-A:去掉 max-w-md 硬上限,截断只发生在列宽不足时(悬停 title 看全文)
          <span className="data-text block truncate text-sm text-ink" title={row.url}>
            {rest === '' ? (
              row.url
            ) : (
              <>
                <span className="font-medium">{host}</span>
                <span className="text-ink-muted">{rest}</span>
              </>
            )}
          </span>
        )
      },
    },
    {
      key: 'season',
      header: strings.rssSources.season,
      // 批次三:类型徽标(season=季绑定 / aggregate=聚合);聚合源不绑季,
      // 副行展示全局规则摘要(悬停看全量);解析不到的旧数据回显原 season id
      render: (row) => {
        if (row.kind === 'aggregate') {
          const rules = [row.include_keywords, row.exclude_keywords]
            .filter((rule): rule is string => Boolean(rule && rule.trim() !== ''))
            .join(' / ')
          return (
            <span className="flex flex-col items-start gap-1">
              <Badge tone="info" mark>
                {strings.rssSources.aggregateBadge}
              </Badge>
              {rules !== '' && (
                <span className="data-text max-w-48 truncate text-xs text-ink-muted" title={rules}>
                  {rules}
                </span>
              )}
            </span>
          )
        }
        const match = seasonOptions.find((option) => option.id === row.season_id)
        return (
          <span className="flex items-center gap-1.5">
            <Badge tone="neutral">{strings.rssSources.seasonBadge}</Badge>
            <span className="data-text text-sm text-ink">
              {match !== undefined ? match.label : row.season_id}
            </span>
          </span>
        )
      },
    },
    {
      key: 'token',
      header: strings.rssSources.token,
      render: (row) => (
        <Badge tone={row.has_token ? 'success' : 'neutral'} mark>
          {row.has_token ? strings.settings.configured : strings.settings.notConfigured}
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
          <>
            {/* 12-F:行内立即轮询;全页同一时刻只允许一个轮询在途(后端并发互斥) */}
            <Button
              size="sm"
              variant="ghost"
              aria-label={`${strings.ops12f.pollAction} ${row.url}`}
              loading={pollingId === row.id}
              disabled={pollingId !== null}
              onClick={() => void poll(row)}
            >
              <RefreshCw aria-hidden className="h-3.5 w-3.5" />
              {strings.ops12f.pollAction}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditingSource(row)}>
              {strings.rssSources.editTitle}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void remove(row.id)}>
              {strings.common.remove}
            </Button>
          </>
        ),
    },
  ]

  return (
    <>
      {/* 12-IA 弹窗化:标题行右侧「添加源」主按钮(原常驻表单卡移入 Dialog);
          批次三:追加「添加聚合源」次按钮(不绑季的通用源) */}
      <PageTitle
        title={strings.rssSources.title}
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => setAddingAggregate(true)}>
              <Rss aria-hidden className="h-3.5 w-3.5" />
              {strings.rssSources.addAggregate}
            </Button>
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              <Rss aria-hidden className="h-3.5 w-3.5" />
              {strings.common.add}
            </Button>
          </>
        }
      />

      {/* 12-IA:页面定位提示 —— 本页是高级管理入口,日常订阅在「追番」页选番 */}
      <div
        data-testid="rss-advanced-hint"
        className="flex items-center gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-ink-secondary"
      >
        <Info aria-hidden className="h-3.5 w-3.5 shrink-0" />
        {strings.uxfix.rssSourcesAdvancedHint}
      </div>

      {actionError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.common.actionFailed}</strong>
          {actionError}
        </div>
      )}

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
            empty={
              <div>
                <Rss aria-hidden className="mb-2 h-8 w-8 text-ink-muted" />
                <EmptyState title={strings.rssSources.empty} />
              </div>
            }
            footer={
              <span className="text-xs text-ink-secondary data-text">
                {t(strings.common.total, { count: data?.total ?? 0 })}
              </span>
            }
          />
        )}
      </Card>

      {/* 12-IA 弹窗化:添加源弹窗(原常驻表单卡迁入);key 随开关重挂载 → 重开不留旧输入/旧报错 */}
      <AddSourceDialog
        key={adding ? 'adding-open' : 'adding-closed'}
        open={adding}
        onOpenChange={setAdding}
        onDone={reload}
        seasonOptions={seasonOptions}
      />

      {/* 批次三:添加聚合源弹窗;key 随开关重挂载 → 重开不留旧输入/旧报错 */}
      <AddAggregateDialog
        key={addingAggregate ? 'agg-open' : 'agg-closed'}
        open={addingAggregate}
        onOpenChange={setAddingAggregate}
        onDone={reload}
      />

      {editingSource !== null && (
        <EditSourceDrawer
          source={editingSource}
          onDone={reload}
          onClose={() => setEditingSource(null)}
        />
      )}
    </>
  )
}
