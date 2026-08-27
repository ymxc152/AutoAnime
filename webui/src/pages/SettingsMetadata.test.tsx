import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './ConsolePages'

afterEach(cleanup)

const { apiGet, apiPatch, apiPut, openaiState } = vi.hoisted(() => {
  const openaiState = {
    enabled: false,
    enabled_revision: 1,
    base_url: 'https://api.openai.com',
    base_url_revision: 1,
    model: 'gpt-4.1-mini',
    model_revision: 1,
    timeout: 30,
    timeout_revision: 1,
    api_key_configured: false,
    ready: false,
    review_enabled: false,
    review_enabled_revision: 1,
    parse_agent_mode: 'off',
    parse_agent_mode_revision: 1,
  }
  return {
    apiGet: vi.fn(async (path: string) =>
      path === '/settings'
        ? {
            items: [],
            secrets: [],
            security: {},
            openai: openaiState,
            metadata: {
              bangumi_enabled: false,
              bangumi_enabled_revision: 1,
              tmdb_enabled: false,
              tmdb_enabled_revision: 1,
              timeout: 12,
              timeout_revision: 1,
              tmdb_api_key_configured: false,
            },
          }
        : { items: [] },
    ),
    apiPatch: vi.fn(async () => ({})),
    apiPut: vi.fn(async () => ({})),
    openaiState,
  }
})

vi.mock('../api/client', () => ({
  api: { get: apiGet, post: vi.fn(), patch: apiPatch, put: apiPut, text: vi.fn() },
}))

beforeEach(() => {
  Object.assign(openaiState, {
    enabled: false,
    enabled_revision: 1,
    base_url: 'https://api.openai.com',
    base_url_revision: 1,
    model: 'gpt-4.1-mini',
    model_revision: 1,
    timeout: 30,
    timeout_revision: 1,
    api_key_configured: false,
    ready: false,
    review_enabled: false,
    review_enabled_revision: 1,
    parse_agent_mode: 'off',
    parse_agent_mode_revision: 1,
  })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/settings']}>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('SettingsPage metadata section', () => {
  it('renders the provider toggles and persists the bangumi switch', async () => {
    const user = userEvent.setup()
    renderPage()
    const bangumi = await screen.findByLabelText('使用 Bangumi')
    expect(screen.getByLabelText('使用 TMDB')).toBeInTheDocument()
    expect(screen.getByLabelText('TMDB API Key')).toBeInTheDocument()

    await user.click(bangumi)
    expect(apiPatch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({ key: 'metadata.bangumi_enabled', value: true }),
    )
  })

  it('renders a single AI 参与方式 select defaulting to 关闭 AI', async () => {
    renderPage()
    const select = await screen.findByLabelText('AI 参与方式')
    expect(select).toHaveValue('off')
    // 旧的独立控件不再出现
    expect(screen.queryByLabelText('启用 AI 识别')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('复核代理')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('AI 参与划分名称')).not.toBeInTheDocument()
  })

  it('selecting 所有文件+复核代理 enables AI and persists all three settings', async () => {
    const user = userEvent.setup()
    renderPage()
    const select = await screen.findByLabelText('AI 参与方式')
    await user.selectOptions(select, 'all+review')
    await waitFor(() => {
      expect(apiPatch).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ key: 'openai.enabled', value: true }),
      )
    })
    expect(apiPatch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({ key: 'review.enabled', value: true }),
    )
    expect(apiPatch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({ key: 'parse.agent_mode', value: 'all' }),
    )
  })

  it('reflects already-enabled settings back as the matching AI mode', async () => {
    openaiState.enabled = true
    openaiState.review_enabled = true
    openaiState.parse_agent_mode = 'uncertain'
    renderPage()
    const select = await screen.findByLabelText('AI 参与方式')
    expect(select).toHaveValue('uncertain+review')
  })

  it('switching to 关闭 AI persists all three settings off', async () => {
    openaiState.enabled = true
    openaiState.review_enabled = true
    openaiState.parse_agent_mode = 'all'
    const user = userEvent.setup()
    renderPage()
    const select = await screen.findByLabelText('AI 参与方式')
    expect(select).toHaveValue('all+review')

    await user.selectOptions(select, 'off')
    await waitFor(() => {
      expect(apiPatch).toHaveBeenCalledWith(
        '/settings',
        expect.objectContaining({ key: 'openai.enabled', value: false }),
      )
    })
    expect(apiPatch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({ key: 'review.enabled', value: false }),
    )
    expect(apiPatch).toHaveBeenCalledWith(
      '/settings',
      expect.objectContaining({ key: 'parse.agent_mode', value: 'off' }),
    )
  })
})
