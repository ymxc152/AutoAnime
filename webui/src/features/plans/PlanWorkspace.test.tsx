import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { PlanWorkspace } from './PlanWorkspace'

describe('PlanWorkspace', () => {
  it('selects a row, shows inspector and prevents approval with conflicts', async () => {
    const approve = vi.fn()
    render(<PlanWorkspace plan={{
      id: 7, status: 'draft', revision: 1,
      items: [
        { id: 1, source_path: 'F:\\动漫下载\\A.mkv', destination_path: 'F:\\动漫库\\A.mkv', action: 'link', reason: '', risk_level: 'normal', execution_status: 'pending', source_size: 100 },
        { id: 2, source_path: 'F:\\动漫下载\\B.mkv', destination_path: 'F:\\动漫库\\B.mkv', action: 'conflict', reason: '目标已存在', risk_level: 'high', execution_status: 'conflict', source_size: 200 },
      ],
    }} onApprove={approve} />)
    await userEvent.click(screen.getByText('B.mkv'))
    expect(screen.getByRole('complementary')).toHaveTextContent('目标已存在')
    expect(screen.getByRole('button', { name: '批准并执行' })).toBeDisabled()
  })
})
