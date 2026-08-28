import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ProfilesPage } from './ConsolePages'

const { apiGet, apiPatch, profileFixture } = vi.hoisted(() => {
  const profileFixture = {
    id: 1, name: '默认配置', source_root_id: 1, library_root_id: 2,
    mode: 'copy', execution_policy: 'review_all', min_confidence: 91,
    stability_seconds: 45, watch_enabled: 0, enabled: 1, revision: 2,
    scan_runs: 1, plans: 1,
  }
  return {
    apiGet: vi.fn(async (path: string) => {
      if (path === '/profiles') {
        return { items: [profileFixture] }
      }
      if (path === '/roots') {
        return {
          items: [
            { id: 1, kind: 'source', path: 'F:\\src', enabled: 1 },
            { id: 2, kind: 'library', path: 'F:\\lib', enabled: 1 },
            { id: 3, kind: 'source', path: 'F:\\src2', enabled: 1 },
            { id: 4, kind: 'library', path: 'F:\\lib2', enabled: 1 },
          ],
        }
      }
      return { items: [] }
    }),
    apiPatch: vi.fn(async () => ({})),
    profileFixture,
  }
})

vi.mock('../api/client', () => ({
  api: { get: apiGet, post: vi.fn(), patch: apiPatch, put: vi.fn(), delete: vi.fn(), text: vi.fn() },
}))

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><ProfilesPage /></MemoryRouter></QueryClientProvider>)
}

describe('ProfilesPage edit flow', () => {
  afterEach(cleanup)
  beforeEach(() => {
    apiGet.mockClear()
    apiPatch.mockClear()
    profileFixture.scan_runs = 1
    profileFixture.plans = 1
    profileFixture.enabled = 1
  })

  it('opens an edit window with a visible header and highlighted row, then saves the renamed profile', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('默认配置')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '编辑' }))
    const dialog = screen.getByRole('dialog', { name: '编辑扫描方案' })
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByText(/正在编辑扫描方案「默认配置」/)).toBeInTheDocument()
    const form = within(dialog).getByText(/正在编辑扫描方案「默认配置」/).closest('form')!
    expect(within(form).getByLabelText('下载源')).toHaveValue('1')
    expect(within(form).getByLabelText('媒体库')).toHaveValue('2')
    expect(within(form).getByRole('option', { name: 'F:\\src' })).toBeInTheDocument()
    expect(within(form).getByRole('option', { name: 'F:\\lib' })).toBeInTheDocument()
    const row = document.querySelector('.profile-row')
    expect(row?.classList.contains('active')).toBe(true)

    await user.clear(screen.getByLabelText('配置名称'))
    await user.type(screen.getByLabelText('配置名称'), '新名称')
    await user.selectOptions(within(form).getByLabelText('下载源'), '3')
    await user.selectOptions(within(form).getByLabelText('媒体库'), '4')
    await user.click(screen.getByRole('button', { name: '保存配置' }))

    expect(apiPatch).toHaveBeenCalledWith('/profiles/1', expect.objectContaining({ revision: 2 }))
    const body = (apiPatch.mock.calls[0] as unknown as unknown[])[1] as { patch: Record<string, unknown> }
    expect(body.patch.name).toBe('新名称')
    expect(body.patch.source_root_id).toBe(3)
    expect(body.patch.library_root_id).toBe(4)
    expect(screen.getByText('F:\\src → F:\\lib')).toBeInTheDocument()
  })

  it('opens a create window from the scan plan list', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('默认配置')
    await user.click(screen.getByRole('button', { name: '新建扫描方案' }))
    expect(screen.getByRole('dialog', { name: '新建扫描方案' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建扫描方案' })).toBeInTheDocument()
  })

  it('offers disable instead of delete for a profile with history', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('默认配置')
    const row = document.querySelector('.profile-row') as HTMLElement

    expect(within(row).getByRole('button', { name: '停用' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /删除/ })).not.toBeInTheDocument()

    await user.click(within(row).getByRole('button', { name: '停用' }))
    expect(apiPatch).toHaveBeenCalledWith('/profiles/1', {
      revision: 2,
      patch: { enabled: false },
    })
  })

  it('keeps delete for a profile without history', async () => {
    profileFixture.scan_runs = 0
    profileFixture.plans = 0
    renderPage()
    await screen.findByText('默认配置')
    const row = document.querySelector('.profile-row') as HTMLElement

    expect(within(row).getByRole('button', { name: /删除/ })).toBeInTheDocument()
  })
})
