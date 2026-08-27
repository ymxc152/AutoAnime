import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { DashboardView } from './DashboardView'

afterEach(cleanup)

describe('DashboardView', () => {
  it('renders operational counts, roots and recent activity', () => {
    render(<MemoryRouter><DashboardView data={{
      active_jobs: 2,
      open_reviews: 4,
      conflicts: 1,
      failed_jobs: 0,
      roots: [{ id: 1, kind: 'source', path: 'F:\\动漫下载', health_status: 'healthy', enabled: 1 }],
      recent_jobs: [{ id: 8, job_type: 'scan', status: 'running', current_stage: '识别文件', progress_current: 12, progress_total: 30, created_at: '2026-07-25' }],
    }} /></MemoryRouter>)
    expect(screen.getByText('活动任务')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('F:\\动漫下载')).toBeInTheDocument()
    expect(screen.getByText('识别文件')).toBeInTheDocument()
  })

  it('offers qB webhook setup only when automation counts are explicitly zero', () => {
    render(<MemoryRouter><DashboardView data={{
      active_jobs: 0,
      open_reviews: 0,
      conflicts: 0,
      failed_jobs: 0,
      webhook_count: 0,
      schedule_count: 0,
      learned_aliases: 0,
      recent_titles: ['葬送的芙莉莲'],
      roots: [{ id: 1, kind: 'source', path: 'F:\\动漫下载', health_status: 'healthy' }],
      recent_jobs: [],
    }} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: '配置 qB 通知（默认）' })).toHaveAttribute('href', '/settings?tab=automation')
    expect(screen.getByText('已记住别名')).toBeInTheDocument()
    expect(screen.getByText('葬送的芙莉莲')).toBeInTheDocument()
  })

  it('keeps the original fallback when automation counts are omitted', () => {
    render(<MemoryRouter><DashboardView data={{ active_jobs: 0, open_reviews: 0, conflicts: 0, failed_jobs: 0, roots: [{ id: 1 }], recent_jobs: [] }} /></MemoryRouter>)
    expect(screen.getByRole('link', { name: '开始新的扫描' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '配置 qB 通知（默认）' })).not.toBeInTheDocument()
  })
})
