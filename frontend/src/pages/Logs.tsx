/*
 * Logs —— audit 时间线 + operation_id 分组展开 + 撤销整理按钮。
 * 数据:GET /api/audit(前端按 operation_id 分组);POST /api/organize/{id}/rollback。
 */
import { useCallback, useMemo, useState } from 'react'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { strings } from '../strings'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Input,
  PageTitle,
  StatusMark,
} from '../components'
import { formatDateTime } from '../lib/views'
import type { AuditDto } from '../api/types'

interface AuditGroup {
  operationId: string
  entries: AuditDto[]
  latestAt: string
  rollbackable: boolean
}

function groupByOperation(entries: AuditDto[]): AuditGroup[] {
  const map = new Map<string, AuditDto[]>()
  for (const entry of entries) {
    const list = map.get(entry.operation_id) ?? []
    list.push(entry)
    map.set(entry.operation_id, list)
  }
  return [...map.entries()]
    .map(([operationId, list]) => {
      const sorted = [...list].sort((a, b) => b.created_at.localeCompare(a.created_at))
      return {
        operationId,
        entries: sorted,
        latestAt: sorted[0]?.created_at ?? '',
        rollbackable: sorted.some(
          (e) => e.action.startsWith('organize.') && Object.keys(e.reverse).length > 0,
        ),
      }
    })
    .sort((a, b) => b.latestAt.localeCompare(a.latestAt))
}

function JsonBlock({ label, value }: { label: string; value: Record<string, unknown> }) {
  if (Object.keys(value).length === 0) return null
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-ink-secondary">{label}</p>
      <pre className="data-text mt-1 overflow-x-auto rounded-sm bg-surface-2 px-2 py-1.5 text-xs text-ink">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  )
}

function GroupRow({
  group,
  expanded,
  onToggle,
  rollingBack,
  onRollback,
  rollbackMessage,
}: {
  group: AuditGroup
  expanded: boolean
  onToggle: () => void
  rollingBack: boolean
  onRollback: (operationId: string) => void
  rollbackMessage: string | null
}) {
  return (
    <li className="border-b border-line last:border-b-0">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex min-w-0 items-center gap-2 text-left"
        >
          <StatusMark tone={expanded ? 'primary' : 'neutral'} size={7} />
          <span className="data-text truncate text-sm text-ink" title={group.operationId}>
            {group.operationId}
          </span>
          <Badge>{group.entries.length}</Badge>
          {expanded ? (
            <span className="text-xs text-ink-secondary">{strings.logs.collapseGroup}</span>
          ) : (
            <span className="text-xs text-ink-secondary">
              {strings.logs.expandGroup.replace('{n}', String(group.entries.length))}
            </span>
          )}
        </button>
        <span className="data-text ml-auto text-xs text-ink-secondary">
          {formatDateTime(group.latestAt)}
        </span>
        {rollbackMessage !== null && (
          <span className="text-xs text-success">{rollbackMessage}</span>
        )}
        <Button
          size="sm"
          variant={group.rollbackable ? 'secondary' : 'ghost'}
          disabled={!group.rollbackable}
          title={group.rollbackable ? undefined : strings.logs.rollbackOnlyOrganize}
          loading={rollingBack}
          onClick={() => onRollback(group.operationId)}
        >
          {strings.common.rollback}
        </Button>
      </div>
      {expanded && (
        <ul className="flex flex-col gap-3 bg-surface-2/60 px-4 py-3 md:pl-10">
          {group.entries.map((entry) => (
            <li key={entry.id} className="flex flex-col gap-1.5 border-l border-line pl-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-ink">{entry.action}</span>
                <Badge>
                  {strings.logs.entity}: {entry.entity}
                  {entry.entity_id !== null ? `#${entry.entity_id}` : ''}
                </Badge>
                <Badge tone={entry.actor === 'manual' ? 'warning' : 'neutral'} mark>
                  {entry.actor === 'manual' ? strings.logs.actorManual : strings.logs.actorAuto}
                </Badge>
                <span className="data-text text-xs text-ink-secondary">
                  {formatDateTime(entry.created_at)}
                </span>
              </div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                <JsonBlock label={strings.logs.instruction} value={entry.instruction} />
                <JsonBlock label={strings.logs.reverse} value={entry.reverse} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export function LogsPage() {
  const fetcher = useCallback(() => api.audit.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [filter, setFilter] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [rollingBackId, setRollingBackId] = useState<string | null>(null)
  const [rolledBack, setRolledBack] = useState<string | null>(null)
  const [rollbackError, setRollbackError] = useState<string | null>(null)

  const groups = useMemo(() => {
    const all = groupByOperation(data?.items ?? [])
    if (filter === '') return all
    const needle = filter.toLowerCase()
    return all.filter(
      (g) =>
        g.operationId.toLowerCase().includes(needle) ||
        g.entries.some(
          (e) =>
            e.entity.toLowerCase().includes(needle) || e.action.toLowerCase().includes(needle),
        ),
    )
  }, [data, filter])

  const toggle = (operationId: string): void => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(operationId)) {
        next.delete(operationId)
      } else {
        next.add(operationId)
      }
      return next
    })
  }

  const rollback = async (operationId: string): Promise<void> => {
    setRollingBackId(operationId)
    setRollbackError(null)
    setRolledBack(null)
    try {
      await api.organize.rollback(operationId)
      setRolledBack(operationId)
    } catch (cause) {
      setRollbackError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setRollingBackId(null)
    }
  }

  return (
    <>
      <PageTitle
        title={strings.logs.title}
        actions={
          <Input
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={strings.logs.filterPlaceholder}
            aria-label={strings.logs.filterPlaceholder}
            className="w-64"
          />
        }
      />
      {rollbackError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.common.actionFailed}</strong>
          {rollbackError}
        </div>
      )}
      <Card flush>
        {error !== null ? (
          <div className="p-4">
            <ErrorState message={error} onRetry={reload} />
          </div>
        ) : loading ? (
          <div className="flex flex-col gap-2 p-4">
            <div className="h-10 animate-pulse rounded-sm bg-surface-2" />
            <div className="h-10 animate-pulse rounded-sm bg-surface-2" />
            <div className="h-10 animate-pulse rounded-sm bg-surface-2" />
          </div>
        ) : groups.length === 0 ? (
          <div className="p-4">
            <EmptyState title={strings.logs.empty} />
          </div>
        ) : (
          <ul className="flex flex-col">
            {groups.map((group) => (
              <GroupRow
                key={group.operationId}
                group={group}
                expanded={expandedIds.has(group.operationId)}
                onToggle={() => toggle(group.operationId)}
                rollingBack={rollingBackId === group.operationId}
                onRollback={(id) => void rollback(id)}
                rollbackMessage={rolledBack === group.operationId ? strings.common.rolledBack : null}
              />
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}
