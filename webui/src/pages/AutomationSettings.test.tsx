import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AutomationSettings } from './ConsolePages'

describe('AutomationSettings', () => {
  it('shows configured automation and exposes a newly created token only once', async () => {
    const createWebhook = vi.fn()
    const toggleSchedule = vi.fn()
    render(<AutomationSettings
      profiles={[{ id: 1, name: '默认配置' }]}
      schedules={[{ id: 2, profile_id: 1, kind: 'interval', schedule: { interval_minutes: 15 }, timezone: 'UTC', enabled: 1, revision: 1, next_run_at: '2026-07-30T00:00:00+00:00' }]}
      webhooks={[{ id: 3, name: 'qBittorrent', downloader: 'qbittorrent', profile_id: 1, enabled: 1, revision: 1, last_called_at: '2026-07-29T00:00:00+00:00' }]}
      createdToken="one-time-secret"
      onCreateSchedule={vi.fn()}
      onToggleSchedule={toggleSchedule}
      onCreateWebhook={createWebhook}
      onToggleWebhook={vi.fn()}
    />)

    expect(screen.getByText('每 15 分钟')).toBeInTheDocument()
    expect(screen.getByText('qBittorrent')).toBeInTheDocument()
    expect(screen.getByText('one-time-secret')).toBeInTheDocument()
    expect(screen.getByText(/仅显示一次/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '停用计划' }))
    expect(toggleSchedule).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))
    await userEvent.click(screen.getByRole('button', { name: '创建 Webhook' }))
    expect(createWebhook).toHaveBeenCalledWith(expect.objectContaining({ profile_id: 1 }))
  })
})
