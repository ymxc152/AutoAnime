/*
 * 抽屉(Soft Ink:右侧滑出,12px 圆角左缘,z 层级走 token;
 * Esc / 点击遮罩关闭;焦点圈禁 — Tab 限制在 dialog 内,关闭后归还)。
 */
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  subtitle?: ReactNode
  children: ReactNode
  width?: number
}

export function Drawer({ open, onClose, title, subtitle, children, width = 480 }: DrawerProps) {
  const drawerRef = useRef<HTMLElement | null>(null)
  useFocusTrap(drawerRef, open)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0" style={{ zIndex: 'var(--ink-z-drawer-backdrop)' }}>
      <div
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
        aria-hidden
        data-testid="drawer-backdrop"
      />
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="absolute right-0 top-0 flex h-full flex-col overflow-y-auto bg-surface shadow-soft-lg rounded-l-lg animate-[drawer-in_var(--ink-transition-normal)_ease-out] outline-none"
        style={{ width: `min(${width}px, 100vw)`, zIndex: 'var(--ink-z-drawer)' }}
      >
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {subtitle !== undefined && (
              <p className="mt-0.5 text-xs text-ink-secondary">{subtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="-mr-1 rounded-sm px-1.5 text-lg leading-none text-ink-secondary hover:bg-surface-2 hover:text-ink"
          >
            ×
          </button>
        </header>
        <div className="flex-1 px-4 py-3">{children}</div>
      </aside>
      <style>{`@keyframes drawer-in { from { transform: translateX(24px); opacity: 0 } to { transform: translateX(0); opacity: 1 } }`}</style>
    </div>,
    document.body,
  )
}
