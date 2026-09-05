/*
 * useEvents 单测:断线重连(指数退避)+ last_event_id 续传 + 状态机。
 */
import { act, renderHook } from '@testing-library/react'
import { useEvents } from '../useEvents'
import { FakeEventSource, sseMessage } from '../../test/testUtils'
import type { SseEvent } from '../../api/types'
import type { EventSourceFactory } from '../../api/sse'

function makeFactory(registry: FakeEventSource[]): EventSourceFactory {
  return (url) => {
    const source = new FakeEventSource(url)
    registry.push(source)
    return source
  }
}

describe('useEvents', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('挂载后连接并收到事件', async () => {
    const registry: FakeEventSource[] = []
    const received: SseEvent[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: (e) => received.push(e), factory: makeFactory(registry) }),
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.status).toBe('connecting')

    const source = registry[0]
    expect(source).toBeDefined()
    act(() => source!.open())
    expect(result.current.status).toBe('open')

    act(() => {
      source!.emit(sseMessage({ id: '7', category: 'parse', message: 'L1 命中' }))
    })
    expect(result.current.received).toBe(1)
    expect(received[0]?.category).toBe('parse')
    // 对齐后端契约:id 取 SSE id: 行(lastEventId),ts 为前端本地生成
    expect(received[0]?.id).toBe('7')
    expect(received[0]?.ts).not.toBe('')
    expect(received[0]?.payload).toEqual({})
  })

  it('data 载荷解析 {category,message,payload}(后端无 ts/id 字段)', async () => {
    const registry: FakeEventSource[] = []
    const received: SseEvent[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: (e) => received.push(e), factory: makeFactory(registry) }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const source = registry[0]!
    act(() => source!.open())
    act(() => {
      source!.emit(
        sseMessage({
          id: '42',
          category: 'organize',
          message: 'organize.archived',
          payload: { audit_id: 42, dst: '/library/a.mkv' },
        }),
      )
    })
    expect(result.current.received).toBe(1)
    expect(received[0]?.id).toBe('42')
    expect(received[0]?.message).toBe('organize.archived')
    expect(received[0]?.payload).toEqual({ audit_id: 42, dst: '/library/a.mkv' })
  })

  it('断线后指数退避重连,重连 URL 携带 last_event_id', async () => {
    const registry: FakeEventSource[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: () => {}, factory: makeFactory(registry) }),
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const first = registry[0]!
    act(() => first.open())
    act(() => {
      first.emit(sseMessage({ id: '42', category: 'system', message: '心跳' }))
    })

    // 断线
    act(() => first.fail())
    expect(result.current.status).toBe('reconnecting')
    expect(result.current.attempt).toBe(1)
    expect(first.closed).toBe(true)

    // 退避 1s 后重连,URL 带 last_event_id=42;重连尝试期间仍为 reconnecting 态
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(registry.length).toBe(2)
    expect(registry[1]!.urls[0]).toContain('last_event_id=42')
    expect(result.current.status).toBe('reconnecting')

    // 第二次断线:退避 2s
    act(() => registry[1]!.fail())
    expect(result.current.attempt).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(registry.length).toBe(2) // 2s 未到,不重连
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(registry.length).toBe(3)

    // 重连成功后计数归零(fake timers 下不能用 waitFor,状态同步更新)
    act(() => registry[2]!.open())
    expect(result.current.status).toBe('open')
    act(() => {
      registry[2]!.emit(sseMessage({ id: '43', category: 'system', message: 'ok' }))
    })
    expect(result.current.attempt).toBe(0)
  })

  it('退避封顶 30s', async () => {
    const registry: FakeEventSource[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: () => {}, factory: makeFactory(registry) }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    for (let i = 0; i < 6; i++) {
      const source = registry[registry.length - 1]!
      act(() => source.fail())
      const expectedBackoff = Math.min(1000 * 2 ** i, 30_000)
      expect(result.current.attempt).toBe(i + 1)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(expectedBackoff)
      })
    }
    // 第 7 次断线退避应为 30s 上限
    const source = registry[registry.length - 1]!
    act(() => source.fail())
    expect(result.current.attempt).toBe(7)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999)
    })
    expect(registry.length).toBe(7)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(registry.length).toBe(8)
  })

  it('enabled=false 不建连且状态 closed', () => {
    const registry: FakeEventSource[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: () => {}, enabled: false, factory: makeFactory(registry) }),
    )
    expect(result.current.status).toBe('closed')
    expect(registry.length).toBe(0)
  })

  it('卸载后不再重连', async () => {
    const registry: FakeEventSource[] = []
    const { unmount } = renderHook(() =>
      useEvents({ onEvent: () => {}, factory: makeFactory(registry) }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    act(() => registry[0]!.fail())
    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(registry.length).toBe(1)
  })
})
