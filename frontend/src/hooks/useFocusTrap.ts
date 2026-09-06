/*
 * useFocusTrap —— 模态容器焦点圈禁:
 *   active 时 focus 到容器内第一个 focusable 元素(无则 focus 容器);
 *   Tab/Shift+Tab 全控式循环(jsdom 无原生 Tab 移动,故由 hook 统一计算
 *   下一个/上一个 focusable 并 preventDefault,浏览器与测试行为一致);
 *   清理时 focus 归还触发元素(快照为 body/html 时不归还,避免覆盖
 *   用户当前焦点——如点击关闭按钮本身持有焦点的场景)。
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableOf(container: HTMLElement | null): HTMLElement[] {
  if (container === null) return []
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE))
}

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean): void {
  // 保存触发元素的快照,清理时归还
  const restoreRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!active) return
    restoreRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null

    const container = ref.current
    if (container !== null) {
      const items = focusableOf(container)
      if (items.length > 0) {
        items[0]!.focus()
      } else {
        container.focus()
      }
    }

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== 'Tab') return
      const node = ref.current
      if (node === null) return

      const items = focusableOf(node)
      if (items.length === 0) {
        event.preventDefault()
        node.focus()
        return
      }

      const first = items[0]!
      const last = items[items.length - 1]!
      const current = document.activeElement
      const currentIndex = items.indexOf(current as HTMLElement)

      if (event.shiftKey) {
        // 首个元素/容器自身/容器外 → 循环到最后一个;否则前进一个
        if (current === first || currentIndex === -1) {
          event.preventDefault()
          last.focus()
        } else {
          event.preventDefault()
          items[currentIndex - 1]!.focus()
        }
      } else {
        // 最后一个元素/容器外 → 循环到第一个;否则前进一个
        if (current === last || currentIndex === -1) {
          event.preventDefault()
          first.focus()
        } else {
          event.preventDefault()
          items[currentIndex + 1]!.focus()
        }
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      const restore = restoreRef.current
      restoreRef.current = null
      // body/html 快照说明激活前无明确触发焦点,不强行归还
      if (
        restore !== null &&
        restore !== document.body &&
        restore !== document.documentElement
      ) {
        restore.focus()
      }
    }
  }, [active, ref])
}
