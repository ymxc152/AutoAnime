/*
 * 状态小色标(Soft Ink 硬规则:状态 = 方形小色标 7-10px + 墨色文字,
 * 彩色永远不上正文;拒绝粉彩 pill 徽章与发光点)。
 */
import type { ReactNode } from 'react'

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'primary'

const toneClasses: Record<Tone, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-primary',
  neutral: 'bg-ink-muted',
  primary: 'bg-primary',
}

export function StatusMark({ tone, size = 8 }: { tone: Tone; size?: 7 | 8 | 10 }) {
  const dim = size === 7 ? 'h-[7px] w-[7px]' : size === 10 ? 'h-2.5 w-2.5' : 'h-2 w-2'
  return (
    <span
      aria-hidden
      className={`inline-block shrink-0 rounded-[2px] ${dim} ${toneClasses[tone]}`}
    />
  )
}

export interface StatusDotProps {
  tone: Tone
  /** 墨色标签文字(色彩不上文字本身) */
  label?: ReactNode
  size?: 7 | 8 | 10
  className?: string
}

export function StatusDot({ tone, label, size = 8, className = '' }: StatusDotProps) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <StatusMark tone={tone} size={size} />
      {label !== undefined && <span className="text-ink">{label}</span>}
    </span>
  )
}
