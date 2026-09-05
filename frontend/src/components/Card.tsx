/*
 * 卡片容器(Soft Ink:bg-surface 面 + hairline 边 + 8px 圆角)。
 */
import type { ReactNode } from 'react'

export interface CardProps {
  title?: ReactNode
  description?: ReactNode
  /** 无内边距模式(表格等自带边距的内容) */
  flush?: boolean
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function Card({ title, description, flush = false, actions, children, className = '' }: CardProps) {
  return (
    <section
      className={`rounded-md border border-line bg-surface shadow-soft-sm ${className}`}
    >
      {(title !== undefined || actions !== undefined) && (
        <header className="flex items-start justify-between gap-3 px-4 pt-3 pb-2">
          <div>
            {title !== undefined && <h2 className="text-sm font-medium text-ink">{title}</h2>}
            {description !== undefined && (
              <p className="mt-0.5 text-xs text-ink-secondary">{description}</p>
            )}
          </div>
          {actions !== undefined && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? '' : 'px-4 pb-4'}>{children}</div>
    </section>
  )
}
