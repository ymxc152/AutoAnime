/*
 * 按钮(Soft Ink:主色只进 primary;其余为中性填充/幽灵;危险操作实底红)。
 * 全状态:default / hover / focus-visible(base) / active / disabled / loading。
 */
import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  children: ReactNode
}

const variantClasses: Record<Variant, string> = {
  primary: 'bg-primary text-white hover:bg-primary-hover disabled:opacity-50',
  secondary:
    'bg-surface-2 text-ink hover:bg-surface disabled:opacity-50 border border-line hover:border-transparent',
  ghost: 'bg-transparent text-ink-secondary hover:text-ink hover:bg-surface-2 disabled:opacity-50',
  danger: 'bg-danger text-white hover:opacity-90 disabled:opacity-50',
}

const sizeClasses: Record<Size, string> = {
  sm: 'h-7 px-2 text-xs',
  md: 'h-8 px-3 text-sm',
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  className = '',
  disabled,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-1.5 rounded-sm font-medium transition-colors duration-[var(--ink-transition-fast)] ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...rest}
    >
      {loading && (
        <span
          aria-hidden
          className="inline-block h-3 w-3 animate-spin rounded-full border-[1.5px] border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  )
}
