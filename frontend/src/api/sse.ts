/*
 * SSE 传输层抽象:useEvents 只面向 EventSourceHandle 编程,
 * 真实环境走原生 EventSource 的薄包装,mock 环境/单测注入 mock 实现。
 *
 * 契约假设(见 api/types.ts):token 与 last_event_id 走 query param
 * (B7:EventSource 无法自定义 header);浏览器原生自动重连会带
 * Last-Event-ID 头,手动重连由本模块补 last_event_id 查询参数。
 */

export interface SseMessage {
  data: string
  lastEventId: string
}

export interface EventSourceHandle {
  close(): void
  onOpen(cb: () => void): void
  onMessage(cb: (message: SseMessage) => void): void
  onError(cb: () => void): void
}

export type EventSourceFactory = (url: string) => EventSourceHandle

export const nativeEventSourceFactory: EventSourceFactory = (url) => {
  const source = new EventSource(url)
  return {
    close: () => source.close(),
    onOpen: (cb) => {
      source.onopen = () => cb()
    },
    onMessage: (cb) => {
      source.onmessage = (ev) => cb({ data: ev.data, lastEventId: ev.lastEventId })
    },
    onError: (cb) => {
      source.onerror = () => cb()
    },
  }
}

/** /api/events 地址组装:token + last_event_id 查询参数 */
export function buildEventsUrl(lastEventId?: string | null): string {
  const params = new URLSearchParams()
  const token =
    typeof localStorage !== 'undefined' ? (localStorage.getItem('autoanime-api-token') ?? '') : ''
  if (token) {
    params.set('token', token)
  }
  if (lastEventId) {
    params.set('last_event_id', lastEventId)
  }
  const qs = params.toString()
  return `/api/events${qs ? `?${qs}` : ''}`
}
