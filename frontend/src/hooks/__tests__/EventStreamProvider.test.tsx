/*
 * 回归:SSE 工厂缺省接线。生产装配(不传 factory)必须经 api 层默认工厂
 * 真正发起连接——此前 eventSourceFactory 从未被 import 进 Provider,
 * useEvents.connect 的 `if (!make) return` 静默短路,SSE 恒「连接中」。
 */
import { useEffect } from 'react'
import { render, screen, act } from '@testing-library/react'
import { EventStreamProvider } from '../EventStreamProvider'
import { useEventStream } from '../eventStreamContext'
import { FakeEventSource, sseMessage } from '../../test/testUtils'

const state = vi.hoisted(() => ({ created: [] as FakeEventSource[] }))

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    eventSourceFactory: (url: string) => {
      const source = new FakeEventSource(url)
      state.created.push(source)
      return source
    },
  }
})

function useEventStreamSubscribe(listener: (event: { message: string }) => void): void {
  const { subscribe } = useEventStream()
  useEffect(() => subscribe(listener), [subscribe, listener])
}

describe('EventStreamProvider 工厂缺省接线', () => {
  beforeEach(() => {
    state.created = []
  })

  it('不传 factory 时走 api 层默认工厂并发起连接(生产路径回归)', async () => {
    render(
      <EventStreamProvider>
        <div>ok</div>
      </EventStreamProvider>,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(screen.getByText('ok')).toBeDefined()
    expect(state.created.length).toBeGreaterThan(0)
    expect(state.created[0]!.urls[0]).toContain('/api/events')
  })

  it('显式传入 factory 仍优先(测试注入路径不受影响)', async () => {
    const injected: FakeEventSource[] = []
    render(
      <EventStreamProvider
        factory={(url) => {
          const source = new FakeEventSource(url)
          injected.push(source)
          return source
        }}
      >
        <div>ok</div>
      </EventStreamProvider>,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(injected.length).toBeGreaterThan(0)
    expect(state.created.length).toBe(0)
  })

  it('默认工厂连接后事件广播到订阅者(端到端)', async () => {
    const seen: string[] = []
    function Probe(): null {
      useEventStreamSubscribe((event) => seen.push(event.message))
      return null
    }
    render(
      <EventStreamProvider>
        <Probe />
      </EventStreamProvider>,
    )
    await act(async () => {
      await Promise.resolve()
    })
    const source = state.created[0]!
    act(() => source.open())
    act(() => {
      source.emit(sseMessage({ id: '9', category: 'organize', message: 'episode.organized' }))
    })
    expect(seen).toEqual(['episode.organized'])
  })
})
