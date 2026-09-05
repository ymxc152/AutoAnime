/*
 * 表单基础件(Soft Ink:填充式控件,底色 surface-2,边框只在 focus/error 出现;
 * label ≤500 字重;错误 = danger 边框 + danger 文案说明)。
 */
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from 'react'

// ---------- Field ----------

export interface FieldProps {
  label: ReactNode
  htmlFor?: string
  description?: ReactNode
  error?: ReactNode
  children: ReactNode
  className?: string
}

export function Field({ label, htmlFor, description, error, children, className = '' }: FieldProps) {
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
        {label}
      </label>
      {description !== undefined && <p className="text-xs text-ink-secondary">{description}</p>}
      {children}
      {error !== undefined && <p className="text-xs text-danger">{error}</p>}
    </div>
  )
}

// ---------- Input ----------

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
}

export function Input({ invalid = false, className = '', ...rest }: InputProps) {
  return (
    <input
      className={`h-8 w-full rounded-sm bg-surface-2 px-2.5 text-sm text-ink placeholder:text-ink-muted border ${
        invalid ? 'border-danger' : 'border-transparent focus:border-primary'
      } outline-none transition-colors duration-[var(--ink-transition-fast)] ${className}`}
      {...rest}
    />
  )
}

// ---------- Select ----------

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean
}

export function Select({ invalid = false, className = '', children, ...rest }: SelectProps) {
  return (
    <select
      className={`h-8 w-full rounded-sm bg-surface-2 px-2 text-sm text-ink border ${
        invalid ? 'border-danger' : 'border-transparent focus:border-primary'
      } outline-none transition-colors duration-[var(--ink-transition-fast)] ${className}`}
      {...rest}
    >
      {children}
    </select>
  )
}

// ---------- Switch ----------

export interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  /** 关联 Field 时由 Field 传 id */
  id?: string
  disabled?: boolean
  'aria-label'?: string
}

export function Switch({ checked, onChange, id, disabled = false, ...rest }: SwitchProps) {
  // radius-full 仅允许出现在 switch 轨道上(DESIGN.md)
  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-[18px] w-8 shrink-0 items-center rounded-full transition-colors duration-[var(--ink-transition-fast)] ${
        checked ? 'bg-primary' : 'bg-surface-2 border border-line'
      } ${disabled ? 'opacity-50' : ''}`}
      {...rest}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-soft-sm transition-transform duration-[var(--ink-transition-fast)] ${
          checked ? 'translate-x-[15px]' : 'translate-x-0.5'
        }`}
      />
    </button>
  )
}

// ---------- SettingRow:label + 控件一行(设置页骨架) ----------

export interface SettingRowProps {
  label: ReactNode
  description?: ReactNode
  /** 可选:把 label 关联到控件 id(label 点击聚焦控件) */
  htmlFor?: string
  children: ReactNode
}

export function SettingRow({ label, description, htmlFor, children }: SettingRowProps) {
  return (
    <div className="flex flex-col gap-2 py-3 md:flex-row md:items-center md:justify-between">
      <div className="md:max-w-[60%]">
        {htmlFor !== undefined ? (
          <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
            {label}
          </label>
        ) : (
          <span className="text-sm font-medium text-ink">{label}</span>
        )}
        {description !== undefined && (
          <p className="mt-0.5 text-xs text-ink-secondary">{description}</p>
        )}
      </div>
      <div className="shrink-0 md:w-64">{children}</div>
    </div>
  )
}
