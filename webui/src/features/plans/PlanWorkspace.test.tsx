import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PlanWorkspace, type PlanDetail } from './PlanWorkspace'

const plan: PlanDetail = {
  id: 7, status: 'draft', revision: 1,
  items: [
    { id: 1, source_path: 'F:\\动漫下载\\A.mkv', destination_path: 'F:\\动漫库\\A.mkv', action: 'link', reason: '', risk_level: 'normal', execution_status: 'pending', source_size: 100, decision: null },
    { id: 2, source_path: 'F:\\动漫下载\\B.mkv', destination_path: 'F:\\动漫库\\B.mkv', action: 'conflict', reason: '目标已存在', risk_level: 'high', execution_status: 'conflict', source_size: 200, decision: 'rejected', reject_reason: '目标冲突' },
  ],
}

describe('PlanWorkspace', () => {
  afterEach(() => cleanup())

  it('selects a row, shows inspector and prevents approval with conflicts', async () => {
    const approve = vi.fn()
    const approveItem = vi.fn()
    const rejectItem = vi.fn()
    render(<PlanWorkspace plan={plan} onApprove={approve} onApproveItem={approveItem} onRejectItem={rejectItem} />)
    await userEvent.click(screen.getByText('B.mkv'))
    expect(screen.getByRole('complementary')).toHaveTextContent('目标已存在')
    expect(screen.getByRole('button', { name: '开始整理已批准项（0）' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '全部批准并整理' })).toBeDisabled()
    expect(screen.getAllByText('已拒绝').length).toBeGreaterThan(0)
    await userEvent.click(screen.getAllByRole('button', { name: '批准' })[0])
    expect(approveItem).toHaveBeenCalled()
  })

  it('collects a reject reason in a dialog instead of a native prompt', async () => {
    const user = userEvent.setup()
    const rejectItem = vi.fn()
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('should-not-use')
    render(<PlanWorkspace plan={plan} onApprove={() => undefined} onApproveItem={() => undefined} onRejectItem={rejectItem} />)
    await user.click(screen.getAllByRole('button', { name: '拒绝' })[0])
    expect(prompt).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog', { name: '拒绝这项整理' })
    await user.type(within(dialog).getByLabelText('拒绝原因'), '目标路径不对')
    await user.click(within(dialog).getByRole('button', { name: '确认拒绝' }))
    expect(rejectItem).toHaveBeenCalledWith(1, '目标路径不对')
    expect(screen.queryByRole('dialog', { name: '拒绝这项整理' })).not.toBeInTheDocument()
    prompt.mockRestore()
  })
})
