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

describe('ReviewChat', () => {
  it('opens a bound session, sends a message, and applies a proposal', async () => {
    apiPost.mockImplementation(async (path: string) => {
      if (path === '/agent/sessions') {
        return { id: 4, kind: 'review', target_id: 9, status: 'open', proposal: null, messages: [{ id: 1, role: 'system', content: 'ctx' }] }
      }
      if (path === '/agent/sessions/4/messages') {
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
      if (path === '/agent/sessions/4/apply') {
        return { id: 4, status: 'applied', applied: true, proposal: { title: '葬送的芙莉莲' }, messages: [] }
      }
      return {}
    })
    view()
    await userEvent.click(screen.getByRole('button', { name: '问助手' }))
    expect(screen.getByRole('dialog', { name: '纠错会话' })).toBeInTheDocument()
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agent/sessions', { kind: 'review', target_id: 9 }))
    await userEvent.type(screen.getByLabelText('向助手说明问题'), '标题错了')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText('改为葬送的芙莉莲')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '应用提案' }))
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/agent/sessions/4/apply'))
  })
})
