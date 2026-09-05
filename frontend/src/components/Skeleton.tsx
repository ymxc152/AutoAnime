/*
 * 骨架屏(Soft Ink:加载态用骨架,不用内容区转圈)。
 */
import type { CSSProperties } from 'react'

export function Skeleton({
  className = '',
  style,
}: {
  className?: string
  style?: CSSProperties
}) {
  return (
    <div
      aria-hidden
      style={style}
      className={`animate-pulse rounded-sm bg-surface-2 ${className}`}
    />
  )
}
