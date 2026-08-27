import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Dialog } from './Dialog'

describe('Dialog', () => {
  it('renders a labelled window and closes from overlay, button, or Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const { rerender } = render(
      <Dialog open title="编辑扫描方案" description="在独立窗口中修改" onClose={onClose}>
        <p>表单内容</p>
      </Dialog>,
    )
    expect(screen.getByRole('dialog', { name: '编辑扫描方案' })).toBeInTheDocument()
    expect(screen.getByText('表单内容')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)

    rerender(
      <Dialog open title="编辑扫描方案" onClose={onClose}>
        <p>表单内容</p>
      </Dialog>,
    )
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(2)

    rerender(<Dialog open={false} title="编辑扫描方案" onClose={onClose}><p>表单内容</p></Dialog>)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
