import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AgentRail } from './AgentRail'

describe('AgentRail', () => {
  it('shows the current identify unit and memory count', () => {
    render(<AgentRail
      memoryCount={3}
      recentMemory={[{ alias_key: 'blacktorch', canonical_title: 'BLACK TORCH', source: 'identify_batch' }]}
      events={[
        { id: 1, type: 'identify_started', sequence: 1, message: '开始识别', payload: { units: 2, files: 4, trigger: 'webhook' } },
        { id: 2, type: 'identify_unit', sequence: 2, message: '正在识别：BLACK.TORCH', payload: { folder: 'F:\\下载\\BLACK.TORCH', files: 2, title: 'BLACK TORCH', accepted: true } },
      ]}
    />)
    expect(screen.getByText('正在识别：BLACK TORCH')).toBeInTheDocument()
    expect(screen.getByText('已记住 3 个别名')).toBeInTheDocument()
    expect(screen.getAllByText('BLACK TORCH').length).toBeGreaterThan(0)
  })
})
