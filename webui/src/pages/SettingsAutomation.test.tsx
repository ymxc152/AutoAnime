import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './ConsolePages'

afterEach(cleanup)

const { apiGet, apiPatch, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/profiles') {
      return {
        items: [{
          id: 1, name: '默认配置', source_root_id: 1, library_root_id: 2,
          mode: 'copy', execution_policy: 'review_all', revision: 4,
        }],
      }
    }
    if (path === '/settings') {
      return { items: [], secrets: [], security: {}, openai: {}, metadata: {} }
    }
    return { items: [] }
  }),
  apiPatch: vi.fn(async () => ({})),
  apiPost: vi.fn(async (path: string) => {
    if (path === '/webhook-sources') return { id: 9, token: 'once-token', profile_id: 1, revision: 1 }
    if (path === '/schedules') return { id: 8, profile_id: 1, revision: 1 }
    return {}
  }),
}))

vi.mock('../api/client', () => ({
  api: { get: apiGet, post: apiPost, patch: apiPatch, put: vi.fn(), text: vi.fn() },
}))

function renderAutomation() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/settings?tab=automation']}>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SettingsPage unattended wizard', () => {
  beforeEach(() => {
    apiGet.mockClear()
    apiPatch.mockClear()
    apiPost.mockClear()
  })

  it('creating a webhook patches the bound profile to auto_apply_safe and link', async () => {
    const user = userEvent.setup()
    renderAutomation()
    const createWebhook = await screen.findByRole('button', { name: '创建 Webhook' })
    await waitFor(() => expect(createWebhook).toBeEnabled())
    await user.click(createWebhook)
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        '/webhook-sources',
        expect.objectContaining({ profile_id: 1, downloader: 'qbittorrent' }),
      )
    })
    expect(apiPatch).toHaveBeenCalledWith(
      '/profiles/1',
      expect.objectContaining({
        revision: 4,
        patch: { execution_policy: 'auto_apply_safe', mode: 'link' },
      }),
    )
  })

  it('creating a schedule patches the bound profile to auto_apply_safe and link', async () => {
    const user = userEvent.setup()
    renderAutomation()
    const createSchedule = await screen.findByRole('button', { name: /创建计划/ })
    await waitFor(() => expect(createSchedule).toBeEnabled())
    await user.click(createSchedule)
    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        '/schedules',
        expect.objectContaining({ profile_id: 1 }),
      )
    })
    expect(apiPatch).toHaveBeenCalledWith(
      '/profiles/1',
      expect.objectContaining({
        revision: 4,
        patch: { execution_policy: 'auto_apply_safe', mode: 'link' },
      }),
    )
  })
})
