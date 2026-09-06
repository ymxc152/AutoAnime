/*
 * SSE 连接唯一持有者:挂载一次 useEvents,向订阅者广播事件。
 */
import { useCallback, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { useEvents } from './useEvents'
import { EventStreamContext, type EventListener, type EventStreamValue } from './eventStreamContext'
import { eventSourceFactory as defaultEventSourceFactory } from '../api'
import type { EventSourceFactory } from '../api/sse'

export interface EventStreamProviderProps {
  children: ReactNode
  /** 测试注入;缺省用 api 层按 mock 开关选定的工厂 */
  factory?: EventSourceFactory
  enabled?: boolean
}

export function EventStreamProvider({ children, factory, enabled = true }: EventStreamProviderProps) {
  const listenersRef = useRef<Set<EventListener>>(new Set())

  const onEvent = useCallback(
    (event: Parameters<EventListener>[0]) => {
      for (const listener of listenersRef.current) {
        listener(event)
      }
    },
    [],
  )

  // 缺省必须落到 api 层工厂(按 mock 开关选定):否则生产装配下 factory
  // 为 undefined,useEvents.connect 的 `if (!make) return` 会静默不建连,
  // SSE 永远停留在「连接中」。
  const stream = useEvents({
    onEvent,
    factory: factory ?? defaultEventSourceFactory,
    enabled,
  })

  const subscribe = useCallback((listener: EventListener) => {
    listenersRef.current.add(listener)
    return () => {
      listenersRef.current.delete(listener)
    }
  }, [])

  const value = useMemo<EventStreamValue>(
    () => ({ ...stream, subscribe }),
    [stream, subscribe],
  )

  return <EventStreamContext.Provider value={value}>{children}</EventStreamContext.Provider>
}
