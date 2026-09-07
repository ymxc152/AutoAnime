import { act, render, renderHook, waitFor } from '@testing-library/react'
import { EventStreamProvider } from '../EventStreamProvider'
import { useReloadOnMessages } from '../useReloadOnEvent'
import { FakeEventSource, sseMessage } from '../../test/testUtils'

const state = vi.hoisted(() => ({ sources: [] as FakeEventSource[] }))

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    eventSourceFactory: (url: string) => {
      const source = new FakeEventSource(url)
      state.sources.push(source)
      return source
    },
  }
})

function Probe({ reload, messages }: { reload: () => void; messages: string[] }): null {
  useReloadOnMessages(reload, messages, 500)
  return null
}

describe('useReloadOnMessages', () => {
  beforeEach(() => {
    state.sources = []
  })

  it('匹配消息触发一次 reload,短窗口重复与不匹配事件不触发', async () => {
    const reload = vi.fn()
    render(
      <EventStreamProvider>
        <Probe reload={reload} messages={['episode.organized', 'subscription.created']} />
      </EventStreamProvider>,
    )
    await act(async () => {
      await Promise.resolve()
    })
    const source = state.sources[0]!
    act(() => source.open())
    act(() => source.emit(sseMessage({ id: '1', category: 'organize', message: 'episode.organized' })))
    act(() => source.emit(sseMessage({ id: '2', category: 'organize', message: 'episode.organized' })))
    act(() => source.emit(sseMessage({ id: '3', category: 'parse', message: 'pending.confirm' })))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('reload 变化后仍使用最新回调', async () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = renderHook(
      ({ callback, messages }) => useReloadOnMessages(callback, messages, 0),
      {
        wrapper: EventStreamProvider,
        initialProps: { callback: first, messages: ['subscription.created'] },
      },
    )
    await waitFor(() => expect(state.sources.length).toBeGreaterThan(0))
    rerender({ callback: second, messages: ['subscription.created'] })
    await act(async () => {
      state.sources[0]!.emit(
        sseMessage({ id: '7', category: 'system', message: 'subscription.created' }),
      )
    })
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })
})

