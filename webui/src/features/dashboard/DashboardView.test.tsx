import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DashboardView } from './DashboardView'

describe('DashboardView', () => {
  it('renders operational counts, roots and recent activity', () => {
    render(<DashboardView data={{
      active_jobs: 2,
      open_reviews: 4,
      conflicts: 1,
      failed_jobs: 0,
      roots: [{ id: 1, kind: 'source', path: 'F:\\动漫下载', health_status: 'healthy', enabled: 1 }],
      recent_jobs: [{ id: 8, job_type: 'scan', status: 'running', current_stage: '识别文件', progress_current: 12, progress_total: 30, created_at: '2026-07-25' }],
    }} />)
    expect(screen.getByText('活动任务')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('F:\\动漫下载')).toBeInTheDocument()
    expect(screen.getByText('识别文件')).toBeInTheDocument()
  })
})
