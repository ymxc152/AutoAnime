/*
 * Mock SSE 后端 —— 按剧本循环发事件,支持从 last_event_id 续号,
 * 供 Pipeline 页 SSE 驱动动画与单测使用。
 *
 * data 载荷对齐后端 web/sse.py:只含 {category, message, payload};
 * id 走 SSE id: 行(lastEventId),ts 由前端接收侧本地生成。
 * 剧本消息名对齐后端事件目录(pending.confirmed / organize.rolled_back /
 * subscription.created / rss_source.created…);首条 parse 事件带
 * level/outcome 字段,用于演示 E4 管线接入后的富事件路径动画
 * (当前后端 parse 事件不携带这些字段,前端会优雅降级不画路径)。
 */
import type { EventSourceHandle, EventSourceFactory, SseMessage } from '../api/sse'

interface Script {
  category: string
  message: string
  payload: Record<string, unknown>
}

const SCRIPT: Script[] = [
  {
    category: 'parse',
    message: 'pending.confirmed',
    payload: { raw_name: '[Kamigakari] Kusuriya no Hitorigoto - 16 [1080p].mkv', level: 1, outcome: 'l1_high', confidence: 'high', title: '药屋少女的呢喃', season: 2, episode: 16, audit_id: 41 },
  },
  {
    category: 'download',
    message: 'download.completed',
    payload: { torrent_hash: 'a1b2c3d4', file: '[Kamigakari] Kusuriya no Hitorigoto - 16 [1080p].mkv', state: 'completed' },
  },
  {
    category: 'organize',
    message: 'organize.archived',
    payload: { src: '/downloads/Kusuriya 16.mkv', dst: '/library/药屋少女的呢喃/Season 2/药屋少女的呢喃 - S02E16.1080p.mkv', quality: '1080p', audit_id: 42 },
  },
  {
    category: 'parse',
    message: 'pending.corrected',
    payload: { pending_id: 103, title: '葬送的芙莉莲', audit_id: 43 },
  },
  {
    category: 'organize',
    message: 'organize.rolled_back',
    payload: { audit_id: 44, rolled_back_audit_id: 17, learned: true },
  },
  {
    category: 'parse',
    message: 'pending.rejected',
    payload: { pending_id: 104, audit_id: 45 },
  },
  {
    category: 'system',
    message: 'rss_source.polled',
    payload: { sources: 3, new_items: 12 },
  },
]

export class MockEventSource implements EventSourceHandle {
  private openCallbacks: Array<() => void> = []
  private messageCallbacks: Array<(message: SseMessage) => void> = []
  private errorCallbacks: Array<() => void> = []
  private timer: ReturnType<typeof setInterval> | null = null
  private openTimer: ReturnType<typeof setTimeout> | null = null
  private cursor = 0
  private nextId = 1
  private closed = false

  constructor(url: string, intervalMs = 2000) {
    // 从 last_event_id 续号,模拟服务端重放语义
    const match = /[?&]last_event_id=(\d+)/.exec(url)
    if (match) {
      this.nextId = Number(match[1]) + 1
    }
    this.openTimer = setTimeout(() => {
      if (this.closed) return
      for (const cb of this.openCallbacks) cb()
      // 连接即补发一条,UI 不用干等
      this.emit(SCRIPT[this.cursor % SCRIPT.length]!)
      this.cursor += 1
      this.timer = setInterval(() => {
        if (this.closed) return
        this.emit(SCRIPT[this.cursor % SCRIPT.length]!)
        this.cursor += 1
      }, intervalMs)
    }, 150)
  }

  private emit(script: Script): void {
    // 对齐真实帧:data 只含 {category,message,payload};id 走 lastEventId
    const message: SseMessage = {
      data: JSON.stringify({ category: script.category, message: script.message, payload: script.payload }),
      lastEventId: String(this.nextId),
    }
    this.nextId += 1
    for (const cb of this.messageCallbacks) cb(message)
  }

  close(): void {
    this.closed = true
    if (this.timer) clearInterval(this.timer)
    if (this.openTimer) clearTimeout(this.openTimer)
  }

  onOpen(cb: () => void): void {
    this.openCallbacks.push(cb)
  }

  onMessage(cb: (message: SseMessage) => void): void {
    this.messageCallbacks.push(cb)
  }

  onError(cb: () => void): void {
    this.errorCallbacks.push(cb)
  }
}

export const mockEventSourceFactory = (intervalMs = 2000): EventSourceFactory => (url) =>
  new MockEventSource(url, intervalMs)
