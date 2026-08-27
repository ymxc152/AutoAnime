import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AutomationSettings } from './ConsolePages'

afterEach(cleanup)

describe('AutomationSettings', () => {
  it('shows configured automation and exposes a newly created token only once', async () => {
    const createWebhook = vi.fn()
    const toggleSchedule = vi.fn()
    const deleteSchedule = vi.fn()
    const deleteWebhook = vi.fn()
    render(<AutomationSettings
      profiles={[{ id: 1, name: '默认配置' }]}
      schedules={[{ id: 2, profile_id: 1, kind: 'interval', schedule: { interval_minutes: 15 }, timezone: 'UTC', enabled: 1, revision: 1, next_run_at: '2026-07-30T00:00:00+00:00' }]}
      webhooks={[{ id: 3, name: 'qBittorrent', downloader: 'qbittorrent', profile_id: 1, enabled: 1, revision: 1, last_called_at: '2026-07-29T00:00:00+00:00' }]}
      createdToken="one-time-secret"
      onCreateSchedule={vi.fn()}
      onToggleSchedule={toggleSchedule}
      onDeleteSchedule={deleteSchedule}
      onCreateWebhook={createWebhook}
      onToggleWebhook={vi.fn()}
      onDeleteWebhook={deleteWebhook}
    />)

    expect(screen.getByText('每 15 分钟')).toBeInTheDocument()
    expect(screen.getByText('qBittorrent')).toBeInTheDocument()
    expect(screen.getByText('one-time-secret')).toBeInTheDocument()
    expect(screen.getByText(/仅显示一次/)).toBeInTheDocument()
    expect(screen.getAllByText(/\/api\/v1\/hooks\/downloaders\/one-time-secret/).length).toBeGreaterThan(0)

    await userEvent.click(screen.getByRole('button', { name: '停用计划' }))
    expect(toggleSchedule).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }))
    await userEvent.click(screen.getByRole('button', { name: '创建 Webhook' }))
    expect(createWebhook).toHaveBeenCalledWith(expect.objectContaining({ profile_id: 1 }))

    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await userEvent.click(screen.getByRole('button', { name: '删除计划' }))
    expect(deleteSchedule).toHaveBeenCalledWith(expect.objectContaining({ id: 2, revision: 1 }))
    await userEvent.click(screen.getByRole('button', { name: '删除 Webhook' }))
    expect(deleteWebhook).toHaveBeenCalledWith(expect.objectContaining({ id: 3, revision: 1 }))
  })

  it('creates a daily schedule at the selected local time', async () => {
    const createSchedule = vi.fn()
    render(<AutomationSettings
      profiles={[{ id: 1, name: '默认配置' }]}
      schedules={[]}
      webhooks={[]}
      createdToken=""
      onCreateSchedule={createSchedule}
      onToggleSchedule={vi.fn()}
      onDeleteSchedule={vi.fn()}
      onCreateWebhook={vi.fn()}
      onToggleWebhook={vi.fn()}
      onDeleteWebhook={vi.fn()}
    />)

    await userEvent.selectOptions(screen.getByRole('combobox', { name: '计划类型' }), 'daily')
    const timeInput = screen.getByLabelText('每天时间')
    await userEvent.clear(timeInput)
    await userEvent.type(timeInput, '18:30')
    await userEvent.click(screen.getByRole('button', { name: /创建计划/ }))
    expect(createSchedule).toHaveBeenCalledWith(expect.objectContaining({
      profile_id: 1,
      kind: 'daily',
      schedule: { time: '18:30' },
      timezone: 'Asia/Shanghai',
    }))
  })
})
