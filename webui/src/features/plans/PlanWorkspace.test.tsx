import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { PlanWorkspace } from './PlanWorkspace'

describe('PlanWorkspace', () => {
  it('selects a row, shows inspector and prevents approval with conflicts', async () => {
    const approve = vi.fn()
    const approveItem = vi.fn()
    const rejectItem = vi.fn()
    render(<PlanWorkspace plan={{
      id: 7, status: 'draft', revision: 1,
      items: [
        { id: 1, source_path: 'F:\\动漫下载\\A.mkv', destination_path: 'F:\\动漫库\\A.mkv', action: 'link', reason: '', risk_level: 'normal', execution_status: 'pending', source_size: 100, decision: null },
        { id: 2, source_path: 'F:\\动漫下载\\B.mkv', destination_path: 'F:\\动漫库\\B.mkv', action: 'conflict', reason: '目标已存在', risk_level: 'high', execution_status: 'conflict', source_size: 200, decision: 'rejected', reject_reason: '目标冲突' },
      ],
    }} onApprove={approve} onApproveItem={approveItem} onRejectItem={rejectItem} />)
    await userEvent.click(screen.getByText('B.mkv'))
    expect(screen.getByRole('complementary')).toHaveTextContent('目标已存在')
    expect(screen.getByRole('button', { name: '开始整理已批准项（0）' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '全部批准并整理' })).toBeDisabled()
    expect(screen.getAllByText('已拒绝').length).toBeGreaterThan(0)
    await userEvent.click(screen.getAllByRole('button', { name: '批准' })[0])
    expect(approveItem).toHaveBeenCalled()
  })
})
