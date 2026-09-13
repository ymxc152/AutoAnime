/*
 * Logs —— 用户可读的审计时间线 + 撤销整理(对齐后端契约):
 * 组列表来自 GET /api/audit/operations(后端已按 operation_id 分组,最新组在前);
 * 展开时按 operation_id 拉取明细行(GET /api/audit?operation_id=…);
 * 撤销 POST /api/organize/{id}/rollback 的 {id} 是数值 audit 行 id,
 * 组级撤销取该组最新一条 audit 行 id(last_audit_id)。404/409 语义由后端给。
 *
 * 普通用户可读化改造:
 *  - 时间列:组行 last_created_at / 明细行 created_at,本地时区 'MM-DD HH:mm';
 *    字段未上线(0008 迁移前历史行/旧后端)为 null/undefined → 显示「—」,不报错。
 *  - 中文事件:entity/action 本地映射(枚举以 autoanime 后端实际写入值为准),
 *    展示形态「动作 · 对象」(如「归档 · 剧集」);未知值原样回退英文。
 *  - operation_id 降级:主视觉不再是哈希,只保留 8 位前缀次要文本
 *    (title 悬浮看完整值,复制按钮保留);行内主信息 = 时间 + 中文事件 + 条数。
 */
import { useCallback, useState } from 'react'
import { Copy, Undo2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '../api'
import { confirmDialog } from '../lib/confirm'
import { useApi } from '../hooks/useApi'
import { useReloadOnEvent } from '../hooks/useReloadOnEvent'
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

// ---------- 中文事件映射(枚举对齐 autoanime 后端实际写入值;未知值原样回退英文) ----------

/** 对象(entity)→ 中文 */
const ENTITY_LABELS: Record<string, string> = {
  episode: '剧集',
  series: '订阅',
  pending_queue: '待确认',
  parse_memory: '识别记忆',
  bypass_list: '例外名单',
  release: '发布物',
  arbiter: '仲裁',
  settings: '设置',
  rss_sources: 'RSS 源',
  season_calendar: '选番日历',
}

/** 动作(action)→ 中文 */
const ACTION_LABELS: Record<string, string> = {
  // 整理 / 归档(organize/archive.py、CLI import、reparse)
  'episode.organized': '归档',
  'organize.skipped': '跳过归档',
  'upgrade.completed': '洗版完成',
  'upgrade.rejected': '放弃洗版',
  'mismatch.reattached': '错配修正',
  'mismatch.quarantined': '错配隔离',
  subscribed_fast_path: '订阅直通',
  // 识别 / 学习(memory/governance.py、web/learning.py、pending)
  memory_hit: '命中记忆',
  demote_pending: '降级待确认',
  deprecate: '弃用记忆',
  bypass_add: '登记例外',
  pending_confirm: '确认识别',
  pending_correct: '纠正识别',
  pending_reject: '拒绝识别',
  rollback: '撤销整理',
  // 订阅(subscriptions.py)
  subscription_created: '添加订阅',
  subscription_updated: '更新订阅',
  subscription_deleted: '删除订阅',
  // RSS 源(rss_sources.py)
  rss_source_created: '添加 RSS 源',
  rss_source_updated: '更新 RSS 源',
  rss_source_deleted: '删除 RSS 源',
  rss_source_polled: '轮询 RSS 源',
  // 设置(settings.py)
  'settings.updated': '更新设置',
  'settings.notify_test': '通知通道测试',
  'settings.qbit_test': '下载器测试',
  // 季度选番(calendar.py)
  season_calendar_viewed: '查看选番日历',
  season_browse_viewed: '浏览季度番剧',
  // 仲裁审计(pipeline/l3/arbiter.py R8 枚举)
  field_conflict: '字段冲突仲裁',
  level_upgraded: '置信升档',
  season_disambiguated: '季数消歧',
  season_disambiguation_rejected: '季数消歧否决',
  l3_unavailable: 'L3 不可用',
}

/** 未知值原样回退英文,容忍后端新增枚举 */
function entityLabel(entity: string): string {
  return ENTITY_LABELS[entity] ?? entity
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

/** 明细行事件:「动作 · 对象」(如「归档 · 剧集」);entity 为空时只显示动作 */
function eventLabel(action: string, entity: string): string {
  const entityText = entityLabel(entity)
  return entityText === '' ? actionLabel(action) : `${actionLabel(action)} · ${entityText}`
}

/** 组级事件摘要:组内动作/对象各取并集(通常各 1 项) */
function eventSummary(actions: string[], entities: string[]): string {
  const actionText = actions.map(actionLabel).join('、')
  const entityText = entities.map(entityLabel).filter(Boolean).join('、')
  return entityText === '' ? actionText : `${actionText} · ${entityText}`
}

// ---------- 时间格式化 ----------

/** 时间缺省占位符(后端 created_at 未上线/0008 迁移前历史行;strings 无对应键,取字面量) */
const TIME_FALLBACK = '—'

/** ISO → 本地时区 'MM-DD HH:mm';null/undefined → '—';非法串原样展示(便于排查) */
function formatLogTime(iso: string | null | undefined): string {
  if (!iso) return TIME_FALLBACK
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 32 位哈希降级为 8 位前缀(完整值走 title 悬浮);短 id 原样 */
function shortOperationId(operationId: string): string {
  return operationId.length <= 8 ? operationId : operationId.slice(0, 8)
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

/** 单组展开后的审计明细行(懒加载) */
function GroupEntries({
  operationId,
  onRolledBack,
}: {
  operationId: string
  /** 12-F:行级撤销成功后同步刷新上层组列表(撤销本身落新审计组) */
  onRolledBack?: () => void
}) {
  const fetcher = useCallback(
    () => api.audit.list({ operation_id: operationId, limit: 50 }),
    [operationId],
  )
  const { data, loading, error, reload } = useApi(fetcher)
  const [rollbackBusyId, setRollbackBusyId] = useState<number | null>(null)

  /**
   * 12-F:行级撤销。可撤销口径 = 该明细行自身带非空 reverse 指令
   * (与后端一致:rollbackable 组级判定即「组内最新行 reverse 非空」;
   * 行无 reverse 时后端 rollback 端点必回 409,故据此隐藏入口)。
   */
  const rollbackRow = async (entry: AuditDto): Promise<void> => {
    if (
      !(await confirmDialog(t(strings.logs.rollbackRowConfirm, { id: entry.id })))
    ) {
      return
    }
    setRollbackBusyId(entry.id)
    try {
      await api.organize.rollback(entry.id)
      toast.success(t(strings.logs.rollbackRowDone, { id: entry.id }))
      reload()
      onRolledBack?.()
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setRollbackBusyId(null)
    }
  }

  if (error !== null) {
    return <p className="text-sm font-medium text-danger">{error}</p>
  }
  if (loading) {
    return <p className="text-xs text-ink-secondary">{strings.common.loading}</p>
  }
  const entries: AuditDto[] = data?.items ?? []
  return (
    <ul className="flex flex-col gap-2">
      {entries.map((entry) => (
        <li key={entry.id} className="flex flex-col gap-1.5 border-l border-line pl-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="data-text shrink-0 text-xs text-ink-secondary"
              title={strings.logs.time}
            >
              {formatLogTime(entry.created_at)}
            </span>
            <span className="text-sm text-ink">{eventLabel(entry.action, entry.entity)}</span>
            {entry.entity_id !== null && (
              <Badge title={`${strings.logs.entity} #${entry.entity_id}`}>
                #{entry.entity_id}
              </Badge>
            )}
            <Badge tone={entry.actor === 'manual' ? 'warning' : 'neutral'} mark>
              {entry.actor === 'manual' ? strings.logs.actorManual : strings.logs.actorAuto}
            </Badge>
            <span className="data-text text-xs text-ink-secondary">#{entry.id}</span>
            {Object.keys(entry.reverse).length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-1.5"
                title={strings.logs.rollbackHint}
                loading={rollbackBusyId === entry.id}
                onClick={() => void rollbackRow(entry)}
              >
                <Undo2 aria-hidden className="h-3.5 w-3.5" />
                {strings.logs.rollbackRow}
              </Button>
            )}
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
  onRolledBack,
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
  /** 12-F:透传给明细行,行级撤销成功后刷新组列表 */
  onRolledBack?: () => void
}) {
  // 复制操作 ID:jsdom 等环境无 clipboard API 时静默跳过,不让测试/降级环境报错。
  const copyOperationId = (): void => {
    try {
      const clip = navigator.clipboard
      if (clip === undefined || typeof clip.writeText !== 'function') return
      void clip.writeText(group.operation_id).then(() => {
        toast.success(strings.common.copied)
      })
    } catch {
      // 无 clipboard:忽略
    }
  }

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
          <span
            className="data-text shrink-0 text-xs text-ink-secondary"
            title={strings.logs.time}
          >
            {formatLogTime(group.last_created_at)}
          </span>
          <span className="truncate text-sm text-ink">
            {eventSummary(group.actions, group.entities)}
          </span>
          <Badge>{group.rows}</Badge>
          <span
            className="data-text truncate text-xs text-ink-secondary"
            title={group.operation_id}
          >
            {shortOperationId(group.operation_id)}
          </span>
          {expanded ? (
            <span className="text-xs text-ink-secondary">{strings.logs.collapseGroup}</span>
          ) : (
            <span className="text-xs text-ink-secondary">
              {t(strings.logs.expandGroup, { n: group.rows })}
            </span>
          )}
        </button>
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-1.5"
          aria-label={strings.logs.copyOperationId}
          title={strings.logs.copyOperationId}
          onClick={copyOperationId}
        >
          <Copy aria-hidden className="h-3.5 w-3.5" />
        </Button>
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
              <Undo2 aria-hidden className="h-3.5 w-3.5" />
              {strings.common.rollback}
            </Button>
          )
        ) : null}
      </div>
      {expanded && (
        <div className="flex flex-col gap-3 bg-surface-2/60 px-4 py-2.5 md:pl-10">
          <GroupEntries operationId={group.operation_id} onRolledBack={onRolledBack} />
        </div>
      )}
    </li>
  )
}

export function LogsPage() {
  const fetcher = useCallback(() => api.auditOperations.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  useReloadOnEvent(reload)
  const [filter, setFilter] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [rollingBackId, setRollingBackId] = useState<string | null>(null)
  const [confirmRollbackId, setConfirmRollbackId] = useState<string | null>(null)
  const [rolledBack, setRolledBack] = useState<string | null>(null)
  const [rollbackError, setRollbackError] = useState<string | null>(null)

  const groups = data?.items ?? []
  // 搜索仍按原始 operation_id / entity / action 值过滤(与后端存储口径一致)
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
                onRolledBack={reload}
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
