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

// 12-UX:静默断链兜底 —— vite 代理 / nginx 反代下后端进程死亡时断链是静默的,
// EventSource 收不到 onerror,UI 会永远停在「事件流已连接」。服务端心跳 30s,
// 这里取 65s(两个心跳周期 + 余量)无任何消息即判链路已死,主动 close 并走
// 既有退避重连路径。
const STALE_MS = 65_000
const STALE_CHECK_INTERVAL_MS = 5_000

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

/**
 * 解析 SSE data 载荷(后端只发 {category,message,payload}):
 * id 取 SSE id: 行(= audit 行 id,经 lastEventId 传入);ts 由前端
 * 接收时刻本地生成,仅用于最近事件列表的展示排序。
 */
function parseEvent(raw: string, lastEventId: string): SseEvent | null {
  try {
    const parsed = JSON.parse(raw) as Partial<SseEvent>
    if (typeof parsed.category !== 'string') {
      return null
    }
    return {
      id: lastEventId !== '' ? lastEventId : null,
      category: parsed.category,
      message: typeof parsed.message === 'string' ? parsed.message : '',
      payload: typeof parsed.payload === 'object' && parsed.payload !== null ? parsed.payload : {},
      ts: new Date().toISOString(),
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
    // 12-UX:最后收到消息的时刻;connect 与每条消息都刷新,作为静默断链检测基准
    let lastMsgAt = Date.now()

    const scheduleReconnect = (): void => {
      attempts += 1
      setAttempt(attempts)
      setStatus('reconnecting')
      const backoff = Math.min(RETRY_BASE_MS * 2 ** (attempts - 1), RETRY_MAX_MS)
      retryTimer = setTimeout(connect, backoff)
    }

    const connect = (): void => {
      if (disposed) return
      // 新连接给满 STALE_MS 的宽限,避免刚建连就被判死
      lastMsgAt = Date.now()
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
        // 收到任何消息都刷新链路健康基准(12-UX:静默断链兜底)
        lastMsgAt = Date.now()
        if (message.lastEventId) {
          lastEventId = message.lastEventId
        }
        const event = parseEvent(message.data, lastEventId)
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
        scheduleReconnect()
      })
    }

    connect()

    // 12-UX:静默断链兜底 —— open 态超过 STALE_MS 无任何消息(正常心跳 30s)
    // 即判链路已死:主动 close 并走与 onError 相同的退避重连路径。代理层吞掉
    // 断链错误时这是唯一的感知手段;收到任何消息即重置计时,不误判。
    const staleTimer = setInterval(() => {
      if (disposed || handle === null) return
      if (Date.now() - lastMsgAt <= STALE_MS) return
      handle.close()
      handle = null
      scheduleReconnect()
    }, STALE_CHECK_INTERVAL_MS)

    return () => {
      disposed = true
      if (retryTimer) clearTimeout(retryTimer)
      clearInterval(staleTimer)
      handle?.close()
      handle = null
    }
  }, [enabled])

  return useMemo(
    () => ({ status: enabled ? status : 'closed', attempt, received }),
    [status, attempt, received, enabled],
  )
}
