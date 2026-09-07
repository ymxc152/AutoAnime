
import { useCallback, useEffect, useRef } from 'react'
import { useEventStream } from './eventStreamContext'
import type { SseEvent } from '../api/types'

export type EventReloadMatcher = (event: SseEvent) => boolean

/**
 * SSE 事件 → 页面 reload 的统一去重入口：
 * 短窗口内同类事件只触发一次刷新，避免批量任务造成请求风暴。
 */
export function useReloadOnEvent(
  reload: () => void,
  matches: EventReloadMatcher = () => true,
  debounceMs = 500,
): void {
  const { subscribe } = useEventStream()
  const reloadRef = useRef(reload)
  const matchesRef = useRef(matches)
  const lastReloadAtRef = useRef(0)

  useEffect(() => {
    reloadRef.current = reload
    matchesRef.current = matches
  }, [reload, matches])

  useEffect(() => {
    let pendingWhenHidden = false

    const runReload = (): void => {
      lastReloadAtRef.current = Date.now()
      pendingWhenHidden = false
      reloadRef.current()
    }

    const onVisibilityChange = (): void => {
      if (document.visibilityState === 'visible' && pendingWhenHidden) runReload()
    }

    const unsubscribe = subscribe((event) => {
      if (!matchesRef.current(event)) return
      const now = Date.now()
      if (now - lastReloadAtRef.current < debounceMs) return
      if (document.visibilityState === 'hidden') {
        pendingWhenHidden = true
        return
      }
      runReload()
    })
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      unsubscribe()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [subscribe, debounceMs])
}

export function useReloadOnMessages(
  reload: () => void,
  messages: readonly string[],
  debounceMs = 500,
): void {
  const matcher = useCallback<EventReloadMatcher>(
    (event) => messages.includes(event.message),
    [messages],
  )
  useReloadOnEvent(reload, matcher, debounceMs)
}

export function useReloadOnCategories(
  reload: () => void,
  categories: readonly string[],
  debounceMs = 500,
): void {
  const matcher = useCallback<EventReloadMatcher>(
    (event) => categories.includes(event.category),
    [categories],
  )
  useReloadOnEvent(reload, matcher, debounceMs)
}
