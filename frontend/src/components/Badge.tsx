/*
 * 徽标 chip(Soft Ink:hairline 描边 + 方形语义小色标 + 墨色文字;
 * 数字一律 data-text mono + tabular-nums。不是粉彩底 pill。)
 */
import type { ReactNode } from 'react'
import { StatusMark, type Tone } from './StatusDot'

export interface BadgeProps {
  tone?: Tone
  /** 数字/代码内容自动套 data-text */
  children: ReactNode
  mark?: boolean
  className?: string
  title?: string
}

export function Badge({ tone = 'neutral', children, mark = false, className = '', title }: BadgeProps) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-sm border border-line bg-surface px-1.5 py-0.5 text-xs text-ink ${className}`}
    >
      {mark && <StatusMark tone={tone} />}
      <span className={typeof children === 'number' ? 'data-text' : undefined}>{children}</span>
    </span>
  )
}
