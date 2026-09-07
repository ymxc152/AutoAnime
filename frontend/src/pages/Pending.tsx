/*
 * Pending —— 待确认队列(人工介入主战场)。对齐后端 PendingOut:
 * 逐字段草稿来自 context(title/season/episode/segment/fansub);
 * 后端不提供证据来源/置信度标注,抽屉不做来源徽标(优雅降级)。
 * 纠正提交 POST /api/pending/{id}/correct:title 必填——未纠正也始终
 * 带上当前 title(触发学习三件套);confirm/reject 另有两键。
 */
import { useCallback, useState } from 'react'
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
  Pagination,
  Select,
  type Column,
} from '../components'
import { formatDateTime, pendingStageLabel } from '../lib/views'
import { Ban, CheckCheck, Inbox, X } from 'lucide-react'
import { Checkbox } from '@/components/ui/checkbox'
import { Separator } from '@/components/ui/separator'
import type { PendingItemDto } from '../api/types'

/** context 草稿值 → 展示文本(缺失显示 —) */
function contextText(item: PendingItemDto, key: string): string {
  const value = item.context[key]
  if (value === undefined || value === null || value === '') return '—'
  return String(value)
}

interface FieldView {
  key: string
  label: string
  value: string
}

function fieldViews(item: PendingItemDto): FieldView[] {
  return [
    { key: 'title', label: strings.pending.fieldTitle, value: contextText(item, 'title') },
    { key: 'season', label: strings.pending.fieldSeason, value: contextText(item, 'season') },
    { key: 'episode', label: strings.pending.fieldEpisode, value: contextText(item, 'episode') },
    { key: 'segment', label: strings.pending.fieldSegment, value: contextText(item, 'segment') },
    { key: 'fansub', label: strings.pending.fieldFansub, value: contextText(item, 'fansub') },
  ]
}

const SEGMENT_OPTIONS = ['episode', 'season_pack', 'movie'] as const

/** 原始 stage 字符串 → Badge tone(按原始枚举判断,非展示文案;未知回退 neutral) */
const stageTone: Record<string, 'info' | 'primary' | 'warning'> = {
  episode: 'info',
  season_pack: 'primary',
  movie: 'warning',
}

/** 纠正表单 + 草稿字段视图抽屉 */
function CorrectDrawer({ item, onDone, onClose }: { item: PendingItemDto; onDone: () => void; onClose: () => void }) {
  // 表单初值 = 行内 context 草稿;title 始终提交(后端 PendingCorrectIn 必填)
  const [form, setForm] = useState({
    title: contextText(item, 'title') === '—' ? '' : contextText(item, 'title'),
    season: contextText(item, 'season') === '—' ? '' : contextText(item, 'season'),
    episode: contextText(item, 'episode') === '—' ? '' : contextText(item, 'episode'),
    segment: contextText(item, 'segment'),
    fansub: contextText(item, 'fansub') === '—' ? '' : contextText(item, 'fansub'),
  })
  const [submitting, setSubmitting] = useState<'correct' | 'confirm' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async (
    action: 'correct' | 'confirm' | 'reject',
  ): Promise<void> => {
    setSubmitting(action)
    setError(null)
    try {
      if (action === 'correct') {
        // 契约:title 必填(未纠正也带原值);数字字段空串=不覆盖(回退草稿)
        await api.pending.correct(item.id, {
          title: form.title,
          ...(form.season !== '' ? { season: Number(form.season) } : {}),
          ...(form.episode !== '' ? { episode: Number(form.episode) } : {}),
          ...(form.segment !== '' ? { segment: form.segment } : {}),
          ...(form.fansub !== '' ? { fansub: form.fansub } : {}),
        })
      } else if (action === 'confirm') {
        await api.pending.confirm(item.id)
      } else {
        await api.pending.reject(item.id)
      }
      onDone()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(null)
    }
  }

  const views = fieldViews(item)

  return (
    <Drawer open onClose={onClose} title={strings.pending.correctFormTitle} subtitle={item.raw_name}>
      <div className="flex flex-col gap-4">
        <section>
          <h3 className="text-sm font-medium text-ink">{strings.pending.parsedFields}</h3>
          <ul className="mt-2 flex flex-col">
            {views.map((view) => (
              <li
                key={view.key}
                className="flex items-center justify-between gap-2 border-b border-line py-2 last:border-b-0"
              >
                <span className="text-sm text-ink-secondary">{view.label}</span>
                <span className="data-text text-sm text-ink">{view.value}</span>
              </li>
            ))}
          </ul>
        </section>

        <Separator />

        <section>
          <h3 className="text-sm font-medium text-ink">{strings.pending.correctFormTitle}</h3>
          <p className="mt-0.5 text-xs text-ink-secondary">{strings.pending.correctFormHint}</p>
          {error !== null && <p className="mt-2 text-xs text-danger">{error}</p>}
          <form
            className="mt-3 grid grid-cols-2 gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              void run('correct')
            }}
          >
            <Field label={strings.pending.fieldTitle} htmlFor="correct-title" className="col-span-2">
              <Input
                id="correct-title"
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
              />
            </Field>
            <Field label={strings.pending.fieldSeason} htmlFor="correct-season">
              <Input
                id="correct-season"
                inputMode="numeric"
                value={form.season}
                onChange={(e) => setForm({ ...form, season: e.target.value.replace(/[^\d]/g, '') })}
              />
            </Field>
            <Field label={strings.pending.fieldEpisode} htmlFor="correct-episode">
              <Input
                id="correct-episode"
                inputMode="numeric"
                value={form.episode}
                onChange={(e) => setForm({ ...form, episode: e.target.value.replace(/[^\d]/g, '') })}
              />
            </Field>
            <Field label={strings.pending.fieldSegment} htmlFor="correct-segment">
              <Select
                id="correct-segment"
                value={form.segment}
                onChange={(e) => setForm({ ...form, segment: e.target.value })}
              >
                {SEGMENT_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {strings.pending.segment[option]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label={strings.pending.fieldFansub} htmlFor="correct-fansub">
              <Input
                id="correct-fansub"
                value={form.fansub}
                onChange={(e) => setForm({ ...form, fansub: e.target.value })}
              />
            </Field>
            <div className="col-span-2 flex flex-wrap gap-2">
              <Button type="submit" variant="primary" loading={submitting === 'correct'}>
                {strings.pending.submitCorrect}
              </Button>
              <Button variant="secondary" loading={submitting === 'confirm'} onClick={() => void run('confirm')}>
                {strings.pending.confirmAction}
              </Button>
              <Button variant="ghost" loading={submitting === 'reject'} onClick={() => void run('reject')}>
                {strings.pending.rejectAction}
              </Button>
            </div>
          </form>
        </section>
      </div>
    </Drawer>
  )
}

/** 每页条数;与后端 ?limit=&offset= 分页契约对齐 */
const PAGE_SIZE = 20

export function PendingPage() {
  const [page, setPage] = useState(1)
  const fetcher = useCallback(
    () =>
      api.pending.list({ status: 'pending', limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    [page],
  )
  const { data, loading, error, reload } = useApi(fetcher)
  useReloadOnMessages(reload, [
    'pending.confirmed',
    'pending.corrected',
    'pending.rejected',
  ])
  const [selected, setSelected] = useState<PendingItemDto | null>(null)
  // 多选(跨页保留已勾选 id;操作成功后剔除)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(new Set())
  const [busyId, setBusyId] = useState<number | null>(null)
  // 批量轻确认:第一次点击只切换按钮文案,第二次点击才执行
  const [batchArm, setBatchArm] = useState<'confirm' | 'reject' | null>(null)
  // 单条拒绝同样走 arm 二次确认(B1:拒绝不可逆,零确认误触无法回退)
  const [armRejectId, setArmRejectId] = useState<number | null>(null)
  const [batchBusy, setBatchBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const items = data?.items ?? []
  const total = data?.total ?? 0

  const changePage = (next: number): void => {
    setPage(next)
    setSelectedIds(new Set())
    setBatchArm(null)
    setArmRejectId(null)
  }

  const removeFromSelection = (id: number): void => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }

  /** 行内快捷确认/拒绝:确认单条不二次确认;拒绝不可逆,走 arm 二次确认(B1) */
  const resolveOne = async (item: PendingItemDto, action: 'confirm' | 'reject'): Promise<void> => {
    setBusyId(item.id)
    setActionError(null)
    try {
      if (action === 'confirm') {
        await api.pending.confirm(item.id)
      } else {
        await api.pending.reject(item.id)
      }
      removeFromSelection(item.id)
      if (selected?.id === item.id) setSelected(null)
      if (items.length === 1 && page > 1) {
        // 处理空当前页:回第一页(fetcher 变化自动重拉),避免停在空页
        setPage(1)
      } else {
        reload()
      }
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  /** 批量确认/拒绝(已过轻确认);allSettled 部分容错:只剔除成功 id,失败项保留可重试 */
  const resolveBatch = async (action: 'confirm' | 'reject'): Promise<void> => {
    const ids = [...selectedIds]
    setBatchBusy(true)
    setActionError(null)
    const results = await Promise.allSettled(
      ids.map((id) => (action === 'confirm' ? api.pending.confirm(id) : api.pending.reject(id))),
    )
    const succeeded: number[] = []
    const failed: number[] = []
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        succeeded.push(ids[index]!)
      } else {
        failed.push(ids[index]!)
      }
    })
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const id of succeeded) {
        next.delete(id)
      }
      return next
    })
    setBatchArm(null)
    if (failed.length > 0) {
      setActionError(t(strings.pending.batchPartialFailed, { n: failed.length }))
    }
    if (selected !== null && succeeded.includes(selected.id)) setSelected(null)
    const pageEmptied = items.length > 0 && items.every((item) => succeeded.includes(item.id))
    if (pageEmptied && page > 1) {
      // 整页处理空:回第一页,避免停在空页
      setPage(1)
    } else {
      reload()
    }
    setBatchBusy(false)
  }

  const toggleOne = (id: number, checked: boolean): void => {
    setBatchArm(null)
    setArmRejectId(null)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(id)
      } else {
        next.delete(id)
      }
      return next
    })
  }

  const allPageSelected = items.length > 0 && items.every((item) => selectedIds.has(item.id))

  const toggleAllPage = (): void => {
    setBatchArm(null)
    setArmRejectId(null)
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const item of items) {
        if (allPageSelected) {
          next.delete(item.id)
        } else {
          next.add(item.id)
        }
      }
      return next
    })
  }

  const columns: Column<PendingItemDto>[] = [
    {
      key: 'select',
      header: (
        <Checkbox
          aria-label={strings.pending.selectAll}
          checked={allPageSelected}
          onCheckedChange={() => toggleAllPage()}
          className="h-3.5 w-3.5"
        />
      ),
      render: (row) => (
        <Checkbox
          aria-label={t(strings.pending.selectRow, { name: row.raw_name })}
          checked={selectedIds.has(row.id)}
          disabled={busyId === row.id}
          onCheckedChange={(checked) => toggleOne(row.id, checked === true)}
          className="h-3.5 w-3.5"
        />
      ),
    },
    {
      key: 'rawName',
      header: strings.pending.rawName,
      sticky: true,
      render: (row) => (
        // 12-A:去掉 max-w-sm 硬上限,截断只发生在列宽不足时(悬停 title 看全文)
        <span className="data-text block truncate text-sm text-ink" title={row.raw_name}>
          {row.raw_name}
        </span>
      ),
    },
    {
      key: 'stage',
      header: strings.pending.stage,
      render: (row) => (
        <Badge tone={stageTone[row.stage] ?? 'neutral'}>{pendingStageLabel(row.stage)}</Badge>
      ),
    },
    {
      key: 'reason',
      header: strings.pending.reason,
      render: (row) => (
        <span
          className="block truncate text-xs text-ink-secondary"
          title={row.reason ?? undefined}
        >
          {row.reason ?? '—'}
        </span>
      ),
    },
    {
      key: 'createdAt',
      header: strings.pending.createdAt,
      render: (row) => (
        <span className="data-text text-xs text-ink-secondary">{formatDateTime(row.created_at)}</span>
      ),
    },
    {
      key: 'action',
      header: '',
      render: (row) => (
        <span className="flex items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            loading={busyId === row.id}
            disabled={busyId !== null && busyId !== row.id}
            onClick={() => void resolveOne(row, 'confirm')}
          >
            {strings.pending.quickConfirm}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className={armRejectId === row.id ? 'text-danger' : undefined}
            disabled={busyId === row.id}
            onClick={() => {
              // 单条拒绝与批量一致走 arm 二次确认(B1):第一次点击只切换文案
              if (armRejectId === row.id) {
                setArmRejectId(null)
                void resolveOne(row, 'reject')
              } else {
                setArmRejectId(row.id)
              }
            }}
          >
            <X aria-hidden className="h-3.5 w-3.5" />
            {armRejectId === row.id
              ? // strings 键本批冻结,文案按设计规范内联
                '确认拒绝'
              : strings.pending.rejectAction}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setSelected(row)}>
            {strings.pending.correctAction}
          </Button>
        </span>
      ),
    },
  ]

  return (
    <>
      <PageTitle title={strings.pending.title} description={strings.pending.queueHint} />

      {actionError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.common.actionFailed}</strong>
          {actionError}
        </div>
      )}

      {/* 批量操作条:选中后才出现;确认/拒绝均需二次点击 */}
      {selectedIds.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface px-3 py-2">
          <span className="data-text text-xs text-ink-secondary">
            {t(strings.pending.selectedCount, { n: selectedIds.size })}
          </span>
          <Button
            size="sm"
            variant="primary"
            loading={batchBusy && batchArm === 'confirm'}
            disabled={batchBusy && batchArm !== 'confirm'}
            onClick={() => {
              if (batchArm === 'confirm') {
                void resolveBatch('confirm')
              } else {
                setBatchArm('confirm')
              }
            }}
          >
            <CheckCheck aria-hidden className="h-3.5 w-3.5" />
            {batchArm === 'confirm'
              ? t(strings.pending.batchConfirmAsk, { n: selectedIds.size })
              : strings.pending.batchConfirm}
          </Button>
          <Button
            size="sm"
            variant="danger"
            loading={batchBusy && batchArm === 'reject'}
            disabled={batchBusy && batchArm !== 'reject'}
            onClick={() => {
              if (batchArm === 'reject') {
                void resolveBatch('reject')
              } else {
                setBatchArm('reject')
              }
            }}
          >
            <Ban aria-hidden className="h-3.5 w-3.5" />
            {batchArm === 'reject'
              ? t(strings.pending.batchRejectAsk, { n: selectedIds.size })
              : strings.pending.batchReject}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSelectedIds(new Set())
              setBatchArm(null)
            }}
          >
            {strings.pending.clearSelection}
          </Button>
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
            rows={items}
            rowKey={(row) => row.id}
            loading={loading}
            empty={
              <EmptyState
                title={
                  <span className="flex items-center gap-2">
                    <Inbox aria-hidden className="h-5 w-5 text-ink-muted" />
                    {strings.pending.empty}
                  </span>
                }
              />
            }
            footer={
              <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={changePage} />
            }
          />
        )}
      </Card>
      {selected !== null && (
        <CorrectDrawer
          item={selected}
          onDone={() => {
            removeFromSelection(selected.id)
            setSelected(null)
            reload()
          }}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  )
}
