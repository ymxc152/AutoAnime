/*
 * Pending —— 待确认队列(人工介入率主战场)。
 * 列表 → 点开抽屉:逐字段 diff 视图,每个字段标注证据来源(name/folder/memory/llm)
 * 与置信度;纠正表单提交 POST /api/pending/{id}/correct(触发学习三件套);
 * 另有按当前结果确认(confirm)与拒绝(reject)。
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
  StatusMark,
  type Column,
} from '../components'
import { formatDateTime } from '../lib/views'
import type { EvidenceSource, PendingItemDto } from '../api/types'

const evidenceTone: Record<EvidenceSource, 'info' | 'neutral' | 'success' | 'warning'> = {
  name: 'info',
  folder: 'neutral',
  memory: 'success',
  llm: 'warning',
}

const evidenceLabels: Record<EvidenceSource, string> = {
  name: strings.pending.evidence.name,
  folder: strings.pending.evidence.folder,
  memory: strings.pending.evidence.memory,
  llm: strings.pending.evidence.llm,
}

interface FieldView {
  key: string
  label: string
  value: string
  source: EvidenceSource
  confidence: string
}

function fieldViews(item: PendingItemDto): FieldView[] {
  return [
    { key: 'title', label: strings.pending.fieldTitle, value: item.parsed.title.value ?? '—', source: item.parsed.title.source, confidence: item.parsed.title.confidence },
    { key: 'season', label: strings.pending.fieldSeason, value: String(item.parsed.season.value ?? '—'), source: item.parsed.season.source, confidence: item.parsed.season.confidence },
    { key: 'episode', label: strings.pending.fieldEpisode, value: String(item.parsed.episode.value ?? '—'), source: item.parsed.episode.source, confidence: item.parsed.episode.confidence },
    { key: 'fansub', label: strings.pending.fieldFansub, value: item.parsed.fansub.value ?? '—', source: item.parsed.fansub.source, confidence: item.parsed.fansub.confidence },
    { key: 'resolution', label: strings.pending.fieldResolution, value: item.parsed.resolution.value ?? '—', source: item.parsed.resolution.source, confidence: item.parsed.resolution.confidence },
  ]
}

/** 纠正表单 + diff 视图抽屉 */
function CorrectDrawer({ item, onDone, onClose }: { item: PendingItemDto; onDone: () => void; onClose: () => void }) {
  const [form, setForm] = useState({
    title: item.parsed.title.value ?? '',
    season: item.parsed.season.value?.toString() ?? '',
    episode: item.parsed.episode.value?.toString() ?? '',
    fansub: item.parsed.fansub.value ?? '',
    resolution: item.parsed.resolution.value ?? '',
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
        await api.pending.correct(item.id, {
          title: form.title === '' ? undefined : form.title,
          season: form.season === '' ? undefined : Number(form.season),
          episode: form.episode === '' ? undefined : Number(form.episode),
          fansub: form.fansub === '' ? undefined : form.fansub,
          resolution: form.resolution === '' ? undefined : form.resolution,
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
                <span className="flex items-center gap-2">
                  <StatusMark tone={evidenceTone[view.source]} size={7} />
                  <span className="text-sm text-ink">{view.label}</span>
                </span>
                <span className="flex items-center gap-2">
                  <span className="data-text text-sm text-ink">{view.value}</span>
                  <Badge tone={evidenceTone[view.source]} mark>
                    {evidenceLabels[view.source]}
                  </Badge>
                  <span className="text-xs text-ink-secondary data-text">{view.confidence}</span>
                </span>
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
            <Field label={strings.pending.fieldFansub} htmlFor="correct-fansub">
              <Input
                id="correct-fansub"
                value={form.fansub}
                onChange={(e) => setForm({ ...form, fansub: e.target.value })}
              />
            </Field>
            <Field label={strings.pending.fieldResolution} htmlFor="correct-resolution">
              <Input
                id="correct-resolution"
                value={form.resolution}
                onChange={(e) => setForm({ ...form, resolution: e.target.value })}
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
