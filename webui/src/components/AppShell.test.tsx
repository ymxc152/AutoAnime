import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('renders the approved navigation and selected state', () => {
    render(
      <MemoryRouter initialEntries={['/plans']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AppShell><div>页面内容</div></AppShell>
      </MemoryRouter>,
    )

    for (const label of [
      '概览', '扫描配置', '任务中心', '审核队列', '整理计划',
      '资料库', '规则与别名', '操作历史', '系统设置',
    ]) {
      expect(screen.getByRole('link', { name: new RegExp(label) })).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: /整理计划/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('main')).toHaveTextContent('页面内容')
  })
})
