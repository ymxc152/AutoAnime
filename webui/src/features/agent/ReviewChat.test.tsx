import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReviewChat } from './ReviewChat'

afterEach(cleanup)

const { apiPost } = vi.hoisted(() => ({
  apiPost: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  api: { get: vi.fn(), post: apiPost, patch: vi.fn(), put: vi.fn(), text: vi.fn() },
}))

function view() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ReviewChat kind="review" targetId={9} /></QueryClientProvider>)
}

const openSession = {
  id: 4, kind: 'review', target_id: 9, status: 'open', proposal: null,
  messages: [{ id: 1, role: 'system', content: 'ctx' }],
}

describe('ReviewChat', () => {
  it('opens a bound session, sends with Enter, and applies a proposal', async () => {
    let releaseMessage: () => void = () => undefined
    let messageCalls = 0
    apiPost.mockImplementation(async (path: string, _body?: unknown, _headers?: unknown, init?: { signal?: AbortSignal }) => {
      if (path === '/agent/sessions') {
        return { ...openSession }
      }
      if (path === '/agent/sessions/4/messages') {
        messageCalls += 1
        if (messageCalls === 1) {
          await new Promise<void>((resolve, reject) => {
            releaseMessage = resolve
            init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
          })
          return {
            id: 4, kind: 'review', target_id: 9, status: 'open',
            proposal: { title: '葬送的芙莉莲', season: 1, episode: 1 },
            messages: [
              { id: 1, role: 'system', content: 'ctx' },
              { id: 2, role: 'user', content: '标题错了' },
              { id: 3, role: 'assistant', content: '改为葬送的芙莉莲', proposal: { title: '葬送的芙莉莲' } },
            ],
          }
        }
        return {
          id: 4, kind: 'review', target_id: 9, status: 'open',
          proposal: { title: '葬送的芙莉莲', season: 1, episode: 1 },
          messages: [
            { id: 1, role: 'system', content: 'ctx' },
            { id: 2, role: 'user', content: '标题错了' },
            { id: 3, role: 'assistant', content: '改为葬送的芙莉莲', proposal: { title: '葬送的芙莉莲' } },
            { id: 4, role: 'user', content: '补充一句' },
            { id: 5, role: 'assistant', content: '已按补充说明更新提案。', proposal: { title: '葬送的芙莉莲' } },
          ],
        }
      }
      if (path === '/agent/sessions/4/apply') {
        return { id: 4, status: 'applied', applied: true, proposal: { title: '葬送的芙莉莲' }, messages: [] }
      }
      return {}
    })
    view()
    await userEvent.click(screen.getByRole('button', { name: '问助手' }))
    expect(screen.getByRole('dialog', { name: '纠错会话' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '对话' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('向助手说明问题')).not.toBeDisabled())
    await userEvent.type(screen.getByLabelText('向助手说明问题'), '标题错了{Enter}')
    expect(screen.getByLabelText('向助手说明问题')).toHaveValue('')
    expect(await screen.findByText('标题错了')).toBeInTheDocument()
    expect(await screen.findByText('正在思考…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument()
    expect(screen.getByLabelText('向助手说明问题')).not.toBeDisabled()
    await userEvent.type(screen.getByLabelText('向助手说明问题'), '补充一句{Enter}')
    expect(await screen.findByText('补充一句')).toBeInTheDocument()
    releaseMessage()
    expect(await screen.findByText('改为葬送的芙莉莲')).toBeInTheDocument()
    expect(screen.getByText('标题：葬送的芙莉莲')).toBeInTheDocument()
    expect(screen.queryByText(/\{/)).not.toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('正在思考…')).not.toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: '应用提案' }))
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agent/sessions/4/apply'))
  })

  it('can stop an in-flight reply', async () => {
    apiPost.mockImplementation(async (path: string, _body?: unknown, _headers?: unknown, init?: { signal?: AbortSignal }) => {
      if (path === '/agent/sessions') return { ...openSession }
      if (path === '/agent/sessions/4/messages') {
        const signal = init?.signal
        if (signal?.aborted) {
          const error = new Error('Aborted')
          error.name = 'AbortError'
          throw error
        }
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            const error = new Error('Aborted')
            error.name = 'AbortError'
            reject(error)
          })
        })
      }
      return {}
    })
    view()
    await userEvent.click(screen.getByRole('button', { name: '问助手' }))
    await waitFor(() => expect(screen.getByLabelText('向助手说明问题')).not.toBeDisabled())
    await userEvent.type(screen.getByLabelText('向助手说明问题'), '停一下{Enter}')
    expect(await screen.findByText('正在思考…')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(await screen.findByText('已中断本次回复。')).toBeInTheDocument()
    expect(screen.queryByText('正在思考…')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送' })).toBeInTheDocument()
  })

  it('keeps the session after closing so later corrections can continue', async () => {
    apiPost.mockImplementation(async (path: string) => {
      if (path === '/agent/sessions') return { ...openSession }
      return {}
    })
    view()
    await userEvent.click(screen.getByRole('button', { name: '问助手' }))
    await waitFor(() => expect(screen.getByLabelText('向助手说明问题')).not.toBeDisabled())
    await userEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(apiPost.mock.calls.some(call => String(call[0]).endsWith('/abandon'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: '问助手' }))
    expect(screen.getByRole('dialog', { name: '纠错会话' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('向助手说明问题')).not.toBeDisabled())
    expect(apiPost.mock.calls.some(call => String(call[0]).endsWith('/abandon'))).toBe(false)
  })
})
