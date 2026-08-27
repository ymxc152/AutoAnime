import { useEffect, useId, type MouseEvent, type PropsWithChildren } from 'react'
import { createPortal } from 'react-dom'

export function Dialog({
  title,
  description,
  open,
  onClose,
  size = 'md',
  children,
}: PropsWithChildren<{
  title: string
  description?: string
  open: boolean
  onClose: () => void
  size?: 'md' | 'lg' | 'xl'
}>) {
  const titleId = useId()
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = previous
      window.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])
  if (!open) return null
  const dismiss = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }
  return createPortal(
    <div className="dialog-overlay" onMouseDown={dismiss}>
      <div className={`dialog-panel dialog-${size}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="dialog-head">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button type="button" className="text-button" onClick={onClose} aria-label="关闭">关闭</button>
        </header>
        <div className="dialog-body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
