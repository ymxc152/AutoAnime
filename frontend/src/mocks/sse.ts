/*
 * Mock SSE 后端 —— 按剧本循环发事件,支持从 last_event_id 续号,
 * 供 Pipeline 页 SSE 驱动动画与单测使用。
 */
import type { EventSourceHandle, EventSourceFactory, SseMessage } from '../api/sse'
import type { SseEvent } from '../api/types'

interface Script {
  category: SseEvent['category']
  message: string
  payload: Record<string, unknown>
}

const SCRIPT: Script[] = [
  { category: 'parse', message: 'L1 高置信命中', payload: { raw_name: '[Kamigakari] Kusuriya no Hitorigoto - 16 [1080p].mkv', level: 1, outcome: 'l1_high', confidence: 'high', title: '药屋少女的呢喃', season: 2, episode: 16 } },
  { category: 'download', message: '下载完成,进入整理', payload: { torrent_hash: 'a1b2c3d4', file: '[Kamigakari] Kusuriya no Hitorigoto - 16 [1080p].mkv', state: 'completed' } },
  { category: 'organize', message: '归档完成', payload: { src: '/downloads/Kusuriya 16.mkv', dst: '/library/药屋少女的呢喃/Season 2/药屋少女的呢喃 - S02E16.1080p.mkv', quality: '1080p' } },
  { category: 'parse', message: 'L2 记忆命中修正', payload: { raw_name: 'Sousou no Frieren - 25 [B-Global][1080p].mkv', level: 2, outcome: 'memory_hit', confidence: 'high', title: '葬送的芙莉莲', season: 1, episode: 25 } },
  { category: 'organize', message: '洗版替换完成', payload: { old: '/library/迷宫饭/Season 1/迷宫饭 - S01E15.720p.mkv', new: '/library/迷宫饭/Season 1/迷宫饭 - S01E15.1080p.mkv', upgrade: true } },
  { category: 'parse', message: '低置信度,转入人工确认', payload: { raw_name: '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv', level: 1, outcome: 'low_confidence', confidence: 'low', title: 'Spy x Family', episode: 6 } },
  { category: 'system', message: 'RSS 轮询完成:3 源,12 新条目', payload: { sources: 3, new_items: 12 } },
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
    const event: SseEvent = {
      id: String(this.nextId),
      category: script.category,
      message: script.message,
      payload: script.payload,
      ts: new Date().toISOString(),
    }
    this.nextId += 1
    const message: SseMessage = { data: JSON.stringify(event), lastEventId: event.id ?? '' }
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
