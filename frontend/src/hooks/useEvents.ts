/*
 * useEvents —— SSE 事件流 hook(Plan §5.2:断线重连 + Last-Event-ID)。
 *
 * 重连策略(手动受控,不用浏览器默认自动重连,以便做退避):
 *   1. onerror 立即 close,按指数退避重连:1s → 2s → 4s → … 封顶 30s
 *   2. 重连 URL 附 last_event_id 查询参数(服务端据此重放最近事件,防漏报);
 *      浏览器原生同源自动重连才会带 Last-Event-ID 头,手动重连只能走 query
 *   3. 收到消息即视为链路健康,退避清零
 *   4. 组件卸载/close() 后不再重连
 * onEvent/factory 经 ref 间接引用,调用方无需记忆化;enabled=false 时不建连。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { buildEventsUrl, type EventSourceFactory, type EventSourceHandle } from '../api/sse'
import type { SseEvent } from '../api/types'

export type EventsStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'

const RETRY_BASE_MS = 1000
const RETRY_MAX_MS = 30_000

export interface UseEventsResult {
  status: EventsStatus
  /** 当前重连尝试次数(链路恢复后归零) */
  attempt: number
  /** 累计收到的事件数(UI 判断流是否活着用) */
  received: number
}

export interface UseEventsOptions {
  onEvent: (event: SseEvent) => void
  enabled?: boolean
  /** 测试注入;缺省用 api 层按 mock 开关选定的工厂 */
  factory?: EventSourceFactory
}

function parseEvent(raw: string): SseEvent | null {
  try {
    const parsed = JSON.parse(raw) as Partial<SseEvent>
    if (typeof parsed.category !== 'string') {
      return null
    }
    return {
      id: typeof parsed.id === 'string' ? parsed.id : null,
      category: parsed.category,
      message: typeof parsed.message === 'string' ? parsed.message : '',
      payload: typeof parsed.payload === 'object' && parsed.payload !== null ? parsed.payload : {},
      ts: typeof parsed.ts === 'string' ? parsed.ts : '',
    }
  } catch {
    return null
  }
}

export function useEvents(options: UseEventsOptions): UseEventsResult {
  const { onEvent, enabled = true, factory } = options
  const onEventRef = useRef(onEvent)
  const factoryRef = useRef(factory)

  // ref 只允许在 effect 中更新(react-hooks/refs 纪律)
  useEffect(() => {
    onEventRef.current = onEvent
    factoryRef.current = factory
  }, [onEvent, factory])

  const [status, setStatus] = useState<EventsStatus>('connecting')
  const [attempt, setAttempt] = useState(0)
  const [received, setReceived] = useState(0)

  useEffect(() => {
    if (!enabled) {
      return
    }

    let disposed = false
    let handle: EventSourceHandle | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0
    let lastEventId = ''

    const connect = (): void => {
      if (disposed) return
      setStatus(attempts === 0 ? 'connecting' : 'reconnecting')
      const make = factoryRef.current
      if (!make) return
      handle = make(buildEventsUrl(lastEventId))

      handle.onOpen(() => {
        if (disposed) return
        setStatus('open')
      })
      handle.onMessage((message) => {
        if (disposed) return
        if (message.lastEventId) {
          lastEventId = message.lastEventId
        }
        const event = parseEvent(message.data)
        if (event) {
          setReceived((n) => n + 1)
          onEventRef.current(event)
        }
        // 收到任何消息都视为链路健康,重置退避
        if (attempts > 0) {
          attempts = 0
          setAttempt(0)
        }
      })
      handle.onError(() => {
        if (disposed) return
        handle?.close()
        handle = null
        attempts += 1
        setAttempt(attempts)
        setStatus('reconnecting')
        const backoff = Math.min(RETRY_BASE_MS * 2 ** (attempts - 1), RETRY_MAX_MS)
        retryTimer = setTimeout(connect, backoff)
      })
    }

    connect()

    return () => {
      disposed = true
      if (retryTimer) clearTimeout(retryTimer)
      handle?.close()
      handle = null
    }
  }, [enabled])

  return useMemo(
    () => ({ status: enabled ? status : 'closed', attempt, received }),
    [status, attempt, received, enabled],
  )
}
