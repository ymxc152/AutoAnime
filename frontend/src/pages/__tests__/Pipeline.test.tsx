/*
 * Pipeline 页渲染冒烟 + SSE(mock 事件源)驱动的文件流动画集成:
 * 事件注入 → 最近事件列表更新 → token 推进 → organize 节点计数累加。
 */
import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PipelinePage } from '../Pipeline'
import { FakeEventSource, renderPage, sseMessage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'
import type { EventSourceFactory } from '../../api/sse'

describe('PipelinePage', () => {
  let registry: FakeEventSource[]

  beforeEach(() => {
    resetMockState()
    registry = []
  })

  function controlledFactory(): EventSourceFactory {
    return (url) => {
      const source = new FakeEventSource(url)
      registry.push(source)
      return source
    }
  }

  function emit(payload: Record<string, unknown>, category = 'parse', message = '测试事件'): void {
    const source = registry[registry.length - 1]
    act(() => {
      source?.emit(sseMessage({ id: String(Math.random()), category, message, payload }))
    })
  }

  it('渲染 7 个管线节点与命中率徽标', async () => {
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    expect(await screen.findByTestId('pipeline-node-L1 本地解析')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-L2 规则记忆')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-L3 LLM 兜底')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-仲裁')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-归档')).toBeInTheDocument()
    // 基线命中率来自 /api/metrics
    await waitFor(() => {
      expect(within(screen.getByTestId('pipeline-node-L1 本地解析')).getByText(/100%/)).toBeInTheDocument()
    })
  })

  it('SSE parse 事件驱动:最近事件列表出现,token 推进后 organize 计数 +1', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderPage(<PipelinePage />, { factory: controlledFactory() })
      // 等 metrics 基线就绪(organize 初始计数 = l1_high + l2_hit = 387)
      await screen.findByTestId('pipeline-node-归档')
      // 等 metrics 基线就绪(organize 初始计数 = l1_high + l2_hit = 387)
      await waitFor(() => {
        expect(Number(screen.getByTestId('node-count-归档').textContent)).toBe(387)
      })
      const before = Number(screen.getByTestId('node-count-归档').textContent)

      emit({ level: 1, outcome: 'l1_high', raw_name: 'demo.mkv' })
      // 最近事件列表出现该消息
      expect(await screen.findByText('测试事件')).toBeInTheDocument()
      // 推进 tick 直至 token 抵达终点(4 段路径)
      for (let i = 0; i < 4; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(900)
        })
      }
      expect(Number(screen.getByTestId('node-count-归档').textContent)).toBe(before + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('低置信事件最终落到人工确认节点', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderPage(<PipelinePage />, { factory: controlledFactory() })
      // pending 节点基线计数为 0;等 metrics 基线落到 L1 节点后再取 pending 当前值
      await screen.findByTestId('pipeline-node-L1 本地解析')
      // 等 metrics 基线就绪
      await waitFor(() => {
        expect(Number(screen.getByTestId('node-count-L1 本地解析').textContent)).toBe(431)
      })
      const before = Number(screen.getByTestId('node-count-人工确认').textContent)

      emit({ level: 1, outcome: 'low_confidence', confidence: 'low' })
      // path: input→l1→l2→arbiter→pending,4 段需要 4 次推进
      for (let i = 0; i < 5; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(900)
        })
      }
      expect(Number(screen.getByTestId('node-count-人工确认').textContent)).toBe(before + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('清空记录按钮重置最近事件列表', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderPage(<PipelinePage />, { factory: controlledFactory() })
      await screen.findByTestId('pipeline-node-归档')
      emit({ level: 1, outcome: 'l1_high' })
      expect(await screen.findByText('测试事件')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '清空记录' }))
      expect(screen.getByText('等待第一个文件进入管线…')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
  it('移动端降级:渲染纵向流程步骤列表(7 个节点)', async () => {
    renderPage(<PipelinePage />)
    expect(await screen.findByText('流程步骤')).toBeInTheDocument()
    // 步骤列表的节点标题(NODE_META 7 个)
    expect(screen.getAllByText('L1 本地解析').length).toBeGreaterThan(0)
    expect(screen.getAllByText('仲裁').length).toBeGreaterThan(0)
    // 步骤列表传 li 序号 1..7(序号徽标)
    expect(screen.getByText('7')).toBeInTheDocument()
  })
})
