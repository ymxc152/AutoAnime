import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ProfileForm } from './ConsolePages'

describe('ProfileForm', () => {
  it('normalizes SQLite integer booleans without losing other profile fields', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn()
    render(
      <ProfileForm
        editing
        initial={{
          id: 7,
          name: '默认配置',
          source_root_id: 1,
          library_root_id: 2,
          mode: 'copy',
          execution_policy: 'review_all',
          min_confidence: 91,
          stability_seconds: 45,
          watch_enabled: 0,
          enabled: 1,
        }}
        roots={[]}
        onSave={onSave}
      />,
    )

    expect(screen.getByLabelText('配置名称')).toHaveValue('默认配置')
    expect(screen.getByLabelText('文件模式')).toHaveValue('copy')
    expect(screen.getByLabelText('最低置信度')).toHaveValue(91)
    expect(screen.getByRole('checkbox', { name: '启用目录监听' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: '启用此配置' })).toBeChecked()

    await user.click(screen.getByRole('button', { name: '保存配置' }))
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      name: '默认配置',
      mode: 'copy',
      min_confidence: 91,
      stability_seconds: 45,
      watch_enabled: false,
      enabled: true,
    }))
  })
})
