/*
 * Logs —— 审计时间线 + 撤销整理(对齐后端契约):
 * 组列表来自 GET /api/audit/operations(后端已按 operation_id 分组,最新组在前);
 * 展开时按 operation_id 拉取明细行(GET /api/audit?operation_id=…);
 * 撤销 POST /api/organize/{id}/rollback 的 {id} 是数值 audit 行 id,
 * 组级撤销取该组最新一条 audit 行 id(last_audit_id)。404/409 语义由后端给。
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
  Input,
  PageTitle,
  StatusMark,
} from '../components'
import type { AuditDto, OperationGroupDto } from '../api/types'

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

/** 单组展开后的审计明细行(懒加载) */
function GroupEntries({ operationId }: { operationId: string }) {
  const fetcher = useCallback(
    () => api.audit.list({ operation_id: operationId, limit: 50 }),
    [operationId],
  )
  const { data, loading, error } = useApi(fetcher)

  if (error !== null) {
    return <p className="text-xs text-danger">{error}</p>
  }
  if (loading) {
    return <p className="text-xs text-ink-secondary">{strings.common.loading}</p>
  }
  const entries: AuditDto[] = data?.items ?? []
  return (
    <ul className="flex flex-col gap-3">
      {entries.map((entry) => (
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
            <span className="data-text text-xs text-ink-secondary">#{entry.id}</span>
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <JsonBlock label={strings.logs.instruction} value={entry.instruction} />
            <JsonBlock label={strings.logs.reverse} value={entry.reverse} />
          </div>
        </li>
      ))}
    </ul>
  )
}

function GroupRow({
  group,
  expanded,
  onToggle,
  rollingBack,
  confirmRollback,
  onArmRollback,
  onCancelRollback,
  onRollback,
  rollbackMessage,
}: {
  group: OperationGroupDto
  expanded: boolean
  onToggle: () => void
  rollingBack: boolean
  confirmRollback: boolean
  onArmRollback: () => void
  onCancelRollback: () => void
  onRollback: (auditId: number) => void
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
          <span className="data-text truncate text-sm text-ink" title={group.operation_id}>
            {group.operation_id}
          </span>
          <Badge>{group.rows}</Badge>
          {expanded ? (
            <span className="text-xs text-ink-secondary">{strings.logs.collapseGroup}</span>
          ) : (
            <span className="text-xs text-ink-secondary">
              {strings.logs.expandGroup.replace('{n}', String(group.rows))}
            </span>
          )}
        </button>
        <span className="flex flex-wrap gap-1">
          {group.actions.map((action) => (
            <Badge key={action} tone="neutral">
              {action}
            </Badge>
          ))}
        </span>
        {rollbackMessage !== null && (
          <span className="text-xs text-success">{rollbackMessage}</span>
        )}
        {/* 撤销以该组最新 audit 行(last_audit_id)为准;后端 rollbackable=false 时直接隐藏入口。
            危险操作:先内联二次确认,文案带条数。 */}
        {group.rollbackable ? (
          confirmRollback ? (
            <span className="flex items-center gap-1.5">
              <span className="text-xs text-ink-secondary">
                {t(strings.logs.rollbackConfirmCount, { n: group.rows })}
              </span>
              <Button
                size="sm"
                variant="danger"
                loading={rollingBack}
                onClick={() => onRollback(group.last_audit_id)}
              >
                {strings.common.confirm}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onCancelRollback()}>
                {strings.common.cancel}
              </Button>
            </span>
          ) : (
            <Button
              size="sm"
              variant="danger"
              title={strings.logs.rollbackHint}
              onClick={onArmRollback}
            >
              {strings.common.rollback}
            </Button>
          )
        ) : null}
      </div>
      {expanded && (
        <div className="flex flex-col gap-3 bg-surface-2/60 px-4 py-3 md:pl-10">
          <GroupEntries operationId={group.operation_id} />
        </div>
      )}
    </li>
  )
}

export function LogsPage() {
  const fetcher = useCallback(() => api.auditOperations.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [filter, setFilter] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [rollingBackId, setRollingBackId] = useState<string | null>(null)
  const [confirmRollbackId, setConfirmRollbackId] = useState<string | null>(null)
  const [rolledBack, setRolledBack] = useState<string | null>(null)
  const [rollbackError, setRollbackError] = useState<string | null>(null)

  const groups = data?.items ?? []
  const visible = filter === ''
    ? groups
    : groups.filter((g) => {
        const needle = filter.toLowerCase()
        return (
          g.operation_id.toLowerCase().includes(needle) ||
          g.entities.some((e) => e.toLowerCase().includes(needle)) ||
          g.actions.some((a) => a.toLowerCase().includes(needle))
        )
      })

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

  const rollback = async (group: OperationGroupDto): Promise<void> => {
    setConfirmRollbackId(null)
    setRollingBackId(group.operation_id)
    setRollbackError(null)
    setRolledBack(null)
    try {
      await api.organize.rollback(group.last_audit_id)
      setRolledBack(group.operation_id)
      // 撤销本身落一条新审计行:刷新组列表
      reload()
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
        ) : visible.length === 0 ? (
          <div className="p-4">
            <EmptyState title={strings.logs.empty} />
          </div>
        ) : (
          <ul className="flex flex-col">
            {visible.map((group) => (
              <GroupRow
                key={group.operation_id}
                group={group}
                expanded={expandedIds.has(group.operation_id)}
                onToggle={() => toggle(group.operation_id)}
                rollingBack={rollingBackId === group.operation_id}
                confirmRollback={confirmRollbackId === group.operation_id}
                onArmRollback={() => setConfirmRollbackId(group.operation_id)}
                onCancelRollback={() => setConfirmRollbackId(null)}
                onRollback={() => void rollback(group)}
                rollbackMessage={
                  rolledBack === group.operation_id ? strings.common.rolledBack : null
                }
              />
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}
