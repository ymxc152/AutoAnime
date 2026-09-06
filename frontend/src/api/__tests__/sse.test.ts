/*
 * 回归:原生 SSE 工厂必须按「命名事件类别」addEventListener 订阅。
 * 后端每帧都带 event: parse/organize/... 类型,原生 onmessage 对命名
 * 事件不触发——此前实现只用 onmessage,导致连接成功但零事件到达。
 */
import { nativeEventSourceFactory } from '../sse'

class StubEventSource {
  static instances: StubEventSource[] = []
  public listeners = new Map<string, Array<(ev: { data: string; lastEventId: string }) => void>>()
  public onopen: (() => void) | null = null
  public onmessage: ((ev: { data: string; lastEventId: string }) => void) | null = null
  public onerror: (() => void) | null = null
  public closed = false
  constructor(public url: string) {
    StubEventSource.instances.push(this)
  }

  addEventListener(
    type: string,
    fn: (ev: { data: string; lastEventId: string }) => void,
  ): void {
    const list = this.listeners.get(type) ?? []
    list.push(fn)
    this.listeners.set(type, list)
  }

  dispatch(type: string, data: string, lastEventId = ''): void {
    for (const fn of this.listeners.get(type) ?? []) fn({ data, lastEventId })
  }

  close(): void {
    this.closed = true
  }
}

describe('nativeEventSourceFactory 命名事件订阅', () => {
  beforeEach(() => {
    StubEventSource.instances = []
  })

  it('命名类别帧(parse)到达 onMessage 回调', () => {
    vi.stubGlobal('EventSource', StubEventSource)
    try {
      const received: Array<{ data: string; lastEventId: string }> = []
      const handle = nativeEventSourceFactory('/api/events')
      handle.onMessage((m) => received.push(m))
      const stub = StubEventSource.instances[0]!
      stub.dispatch('parse', '{"category":"parse","message":"pending.rejected"}', '31')
      expect(received).toEqual([
        { data: '{"category":"parse","message":"pending.rejected"}', lastEventId: '31' },
      ])
      handle.close()
      expect(stub.closed).toBe(true)
      expect(stub).toBe(StubEventSource.instances[0])
    } finally {
      vi.unstubAllGlobals()
      StubEventSource.instances = []
    }
  })

  it('全部六类(system/download/organize/error/notify)均注册监听', () => {
    vi.stubGlobal('EventSource', StubEventSource)
    try {
      const handle = nativeEventSourceFactory('/api/events')
      handle.onMessage(() => {})
      for (const category of ['parse', 'download', 'organize', 'error', 'notify', 'system']) {
        const internal = StubEventSource.instances[0]!
        expect(internal.listeners.get(category)?.length).toBe(1)
      }
      handle.close()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('无 event: 字段的默认消息仍经 onmessage 兜底到达', () => {
    vi.stubGlobal('EventSource', StubEventSource)
    try {
      const received: string[] = []
      const handle = nativeEventSourceFactory('/api/events')
      handle.onMessage((m) => received.push(m.data))
      const internal = StubEventSource.instances[0]!
      internal.onmessage?.({ data: 'plain', lastEventId: '' })
      expect(received).toEqual(['plain'])
      handle.close()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
