import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ActivityPage, InboxPage, SettingsPage } from './ConsolePages'

vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(async (path: string) => {
      if (path === '/settings') return { items: [], secrets: [], security: {}, openai: {} }
      return { items: [] }
    }),
    post: vi.fn(), patch: vi.fn(), put: vi.fn(), text: vi.fn(),
  },
}))

function Location() { return <output data-testid="location">{useLocation().search}</output> }
function view(route: string, node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}>{node}<Location /></MemoryRouter></QueryClientProvider>)
}

describe('console secondary navigation', () => {
  it('defaults inbox to reviews and updates the query string', async () => {
    view('/inbox', <InboxPage />)
    expect(screen.getByRole('button', { name: /需要确认/ })).toHaveClass('active')
    await userEvent.click(screen.getByRole('button', { name: /整理计划/ }))
    expect(screen.getByTestId('location')).toHaveTextContent('?tab=plans')
  })

  it('shows operation activity from its query tab', () => {
    view('/activity?tab=operations', <ActivityPage />)
    expect(screen.getByRole('button', { name: '整理记录' })).toHaveClass('active')
  })

  it('defaults settings to general and opens advanced rules', () => {
    const first = view('/settings', <SettingsPage />)
    expect(screen.getByRole('button', { name: '常用' })).toHaveClass('active')
    first.unmount()
    view('/settings?tab=advanced&panel=rules', <SettingsPage />)
    expect(screen.getByRole('button', { name: '规则与别名' })).toHaveClass('active')
    expect(screen.getByText(/仅供熟悉 AutoAnime 规则格式/)).toBeInTheDocument()
  })
})
