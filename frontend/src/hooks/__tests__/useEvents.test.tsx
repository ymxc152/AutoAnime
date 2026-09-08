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
    vi.unstubAllGlobals()
    localStorage.removeItem('autoanime-api-token')
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

  it('12-UX:静默断链兜底 —— open 态 65s 无消息主动重连,消息恢复后回 open', async () => {
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
      first.emit(sseMessage({ id: '1', category: 'system', message: '心跳' }))
    })
    expect(result.current.status).toBe('open')

    // 服务端死亡且代理静默吞掉断链(onerror 不触发):65s 无任何消息判死。
    // 检查定时器 5s 间隔,70s 处首次满足 >65s;先推进到触发点,再走 1s 退避。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000)
    })
    expect(first.closed).toBe(true)
    expect(result.current.status).toBe('reconnecting')
    expect(result.current.attempt).toBe(1)
    expect(registry.length).toBe(1) // 退避期内不重连

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(registry.length).toBe(2)

    // 新链路消息恢复 → 回 open,重连计数归零
    act(() => registry[1]!.open())
    expect(result.current.status).toBe('open')
    act(() => {
      registry[1]!.emit(sseMessage({ id: '2', category: 'system', message: 'ok' }))
    })
    expect(result.current.attempt).toBe(0)
  })

  it('12-UX:静默计时被消息重置,持续心跳不会误判断链', async () => {
    const registry: FakeEventSource[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: () => {}, factory: makeFactory(registry) }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const source = registry[0]!
    act(() => source.open())

    // 每 60s 一条心跳(短于 65s 静默阈值):累计 180s 也不应触发重连
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })
      act(() => {
        source.emit(sseMessage({ id: String(i), category: 'system', message: '心跳' }))
      })
    }
    expect(result.current.status).toBe('open')
    expect(registry.length).toBe(1)
  })

  it('12-IA P0-A:静默超时 + health 200 → 不重连,连接与状态都不动', async () => {
    // 12-IA P0-A:注释帧心跳不触发 onMessage,健康链路会静默超时 —— 必须
    // health 探测分流而非直接判死。mock:settings 返回心跳 30s,health 200。
    localStorage.setItem('autoanime-api-token', 'tok-1')
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ api_sse_heartbeat_s: 30 }),
        } as unknown as Response
      }
      return { ok: true, status: 200 } as unknown as Response
    })
    vi.stubGlobal('fetch', fetchMock)

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
      first.emit(sseMessage({ id: '1', category: 'system', message: '事件' }))
    })
    expect(result.current.status).toBe('open')

    // 70s 无命名消息(超过 65s 阈值)→ 触发 /api/health 探测而非直接判死
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000)
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/health',
      expect.objectContaining({ headers: { 'X-API-Token': 'tok-1' } }),
    )
    // 探测成功:连接未 close、状态保持 open、attempt 不变
    expect(first.closed).toBe(false)
    expect(result.current.status).toBe('open')
    expect(result.current.attempt).toBe(0)
    expect(registry.length).toBe(1)

    // 静默基准已刷新:再过 60s(health 持续 200)也不误杀
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(first.closed).toBe(false)
    expect(result.current.status).toBe('open')
    expect(registry.length).toBe(1)
  })

  it('12-IA P0-A:静默超时 + health 失败 → 走既有退避重连', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/settings') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ api_sse_heartbeat_s: 30 }),
        } as unknown as Response
      }
      throw new TypeError('health probe unreachable')
    })
    vi.stubGlobal('fetch', fetchMock)

    const registry: FakeEventSource[] = []
    const { result } = renderHook(() =>
      useEvents({ onEvent: () => {}, factory: makeFactory(registry) }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    const first = registry[0]!
    act(() => first.open())

    // 70s 无消息 + health 探测 reject → 判死:close + reconnecting + attempt+1
    await act(async () => {
      await vi.advanceTimersByTimeAsync(70_000)
    })
    expect(first.closed).toBe(true)
    expect(result.current.status).toBe('reconnecting')
    expect(result.current.attempt).toBe(1)
    expect(registry.length).toBe(1) // 退避期内不重连

    // 1s 退避后重建连接
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(registry.length).toBe(2)
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
