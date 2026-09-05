/*
 * 数据表格(分页)+ 反馈件(骨架/空态/错误态/进度条)。
 * 空态 = 左对齐文字 + 一个动作(拒绝圆角方块图标空态)。
 */
import type { ReactNode } from 'react'
import { strings, t } from '../strings'
import { Button } from './Button'

// ---------- DataTable ----------

export interface Column<T> {
  key: string
  header: ReactNode
  render: (row: T) => ReactNode
  /** 数字/等宽列提示(仅语义,样式由内容自行套 data-text) */
  align?: 'left' | 'right'
  width?: string
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string | number
  loading?: boolean
  empty?: ReactNode
  onRowClick?: (row: T) => void
  /** 表格下方的分页/统计区 */
  footer?: ReactNode
}

const skeletonRows = 5

export function DataTable<T>({ columns, rows, rowKey, loading = false, empty, onRowClick, footer }: DataTableProps<T>) {
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  style={col.width !== undefined ? { width: col.width } : undefined}
                  className={`border-b border-line px-3 py-2 text-xs font-medium text-ink-secondary ${
                    col.align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: skeletonRows }, (_, i) => (
                <tr key={`skeleton-${i}`}>
                  {columns.map((col) => (
                    <td key={col.key} className="border-b border-line px-3 py-2.5">
                      <div className="h-3.5 w-3/4 animate-pulse rounded-sm bg-surface-2" />
                    </td>
                  ))}
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-3 py-6">
                  {empty ?? <p className="text-sm text-ink-secondary">{strings.common.empty}</p>}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick !== undefined ? () => onRowClick(row) : undefined}
                  className={`border-b border-line last:border-b-0 ${
                    onRowClick !== undefined ? 'cursor-pointer hover:bg-surface-2' : ''
                  }`}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={`px-3 py-2.5 align-middle ${col.align === 'right' ? 'text-right' : ''}`}
                    >
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {footer !== undefined && (
        <div className="flex items-center justify-between gap-3 border-t border-line px-3 py-2">
          {footer}
        </div>
      )}
    </div>
  )
}

// ---------- Pagination ----------

export interface PaginationProps {
  page: number // 1-based
  pageSize: number
  total: number
  onPageChange: (page: number) => void
}

export function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-ink-secondary data-text">
        {t(strings.common.total, { count: total })}
      </span>
      <div className="ml-auto flex items-center gap-2">
        <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          {strings.common.prevPage}
        </Button>
        <span className="text-xs text-ink-secondary data-text">
          {t(strings.common.pageInfo, { current: page, total: totalPages })}
        </span>
        <Button
          size="sm"
          variant="ghost"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          {strings.common.nextPage}
        </Button>
      </div>
    </div>
  )
}

// ---------- EmptyState ----------

export interface EmptyStateProps {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="py-6">
      <p className="text-sm font-medium text-ink">{title}</p>
      {description !== undefined && (
        <p className="mt-1 max-w-prose text-sm text-ink-secondary">{description}</p>
      )}
      {action !== undefined && <div className="mt-3">{action}</div>}
    </div>
  )
}

// ---------- ErrorState(Soft Ink:hairline 警报,粗体彩色引导词,无图标无色底) ----------

export interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="rounded-md border border-line px-3 py-2.5 text-sm text-ink-secondary"
    >
      <strong className="mr-1.5 text-danger">{strings.common.loadFailed}</strong>
      {message}
      {onRetry !== undefined && (
        <span className="ml-3">
          <Button size="sm" variant="ghost" onClick={onRetry}>
            {strings.common.retry}
          </Button>
        </span>
      )}
    </div>
  )
}

// ---------- ProgressBar ----------

export interface ProgressBarProps {
  /** 0-1 */
  value: number
  tone?: 'primary' | 'success' | 'warning'
  className?: string
}

export function ProgressBar({ value, tone = 'primary', className = '' }: ProgressBarProps) {
  const clamped = Math.min(1, Math.max(0, value))
  const bg = tone === 'primary' ? 'bg-primary' : tone === 'success' ? 'bg-success' : 'bg-warning'
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(clamped * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={`h-1.5 w-full overflow-hidden rounded-sm bg-surface-2 ${className}`}
    >
      <div className={`h-full rounded-sm ${bg}`} style={{ width: `${clamped * 100}%` }} />
    </div>
  )
}

// ---------- PageTitle ----------

export interface PageTitleProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}

export function PageTitle({ title, description, actions }: PageTitleProps) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h1 className="text-base font-semibold text-ink">{title}</h1>
        {description !== undefined && (
          <p className="mt-0.5 text-sm text-ink-secondary">{description}</p>
        )}
      </div>
      {actions !== undefined && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
