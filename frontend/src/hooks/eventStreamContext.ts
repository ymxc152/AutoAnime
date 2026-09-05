/*
 * 全局事件流上下文:App 挂载一次 SSE 连接,各页面订阅事件;
 * Layout 用同一个 context 展示连接状态,避免多条连接。
 * 本文件只放 context 与消费 hook;Provider 组件在 EventStreamProvider.tsx。
 */
import { createContext, useContext } from 'react'
import type { UseEventsResult } from './useEvents'
import type { SseEvent } from '../api/types'

export type EventListener = (event: SseEvent) => void

export interface EventStreamValue extends UseEventsResult {
  subscribe: (listener: EventListener) => () => void
}

export const EventStreamContext = createContext<EventStreamValue | null>(null)

export function useEventStream(): EventStreamValue {
  const value = useContext(EventStreamContext)
  if (value === null) {
    throw new Error('useEventStream 必须在 <EventStreamProvider> 内使用')
  }
  return value
}
