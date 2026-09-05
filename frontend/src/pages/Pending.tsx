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
  type Column,
} from '../components'
import { formatDateTime } from '../lib/views'
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
                <span className="text-sm text-ink">{view.label}</span>
                <span className="data-text text-sm text-ink">{view.value}</span>
              </li>
            ))}
          </ul>
        </section>

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
            <div className="col-span-2 mt-1 flex flex-wrap gap-2">
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

export function PendingPage() {
  const fetcher = useCallback(() => api.pending.list({ status: 'pending', limit: 50 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [selected, setSelected] = useState<PendingItemDto | null>(null)

  const items = data?.items ?? []

  const columns: Column<PendingItemDto>[] = [
    {
      key: 'rawName',
      header: strings.pending.rawName,
      render: (row) => (
        <span className="data-text block max-w-sm truncate text-sm text-ink" title={row.raw_name}>
          {row.raw_name}
        </span>
      ),
    },
    {
      key: 'stage',
      header: strings.pending.stage,
      render: (row) => <Badge>{row.stage}</Badge>,
    },
    {
      key: 'reason',
      header: strings.pending.reason,
      render: (row) => (
        <span className="block max-w-xs truncate text-xs text-ink-secondary" title={row.reason ?? undefined}>
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
        <Button size="sm" variant="secondary" onClick={() => setSelected(row)}>
          {strings.pending.correctAction}
        </Button>
      ),
    },
  ]

  return (
    <>
      <PageTitle title={strings.pending.title} description={strings.pending.queueHint} />
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
            empty={<EmptyState title={strings.pending.empty} />}
            footer={
              <span className="text-xs text-ink-secondary data-text">
                {t(strings.common.total, { count: data?.total ?? 0 })}
              </span>
            }
          />
        )}
      </Card>
      {selected !== null && (
        <CorrectDrawer
          item={selected}
          onDone={() => {
            setSelected(null)
            reload()
          }}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  )
}
