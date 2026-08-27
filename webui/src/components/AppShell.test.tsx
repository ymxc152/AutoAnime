import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('renders the approved navigation and selected state', () => {
    render(
      <MemoryRouter initialEntries={['/inbox?tab=plans']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AppShell><div>页面内容</div></AppShell>
      </MemoryRouter>,
    )

    for (const label of ['首页', '扫描', '待处理', '运行记录', '资料库', '设置']) {
      expect(screen.getByRole('link', { name: new RegExp(label) })).toBeInTheDocument()
    }
    for (const label of ['任务中心', '操作历史', '规则与别名', '整理计划']) {
      expect(screen.queryByRole('link', { name: new RegExp(label) })).not.toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: /待处理/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('main')).toHaveTextContent('页面内容')
  })

  it('shows the signed-in username and logout action', () => {
    const onLogout = vi.fn()
    render(
      <MemoryRouter>
        <AppShell username="admin" onLogout={onLogout}><div>页面内容</div></AppShell>
      </MemoryRouter>,
    )
    expect(screen.getByText('admin')).toBeInTheDocument()
    screen.getByRole('button', { name: '退出登录' }).click()
    expect(onLogout).toHaveBeenCalled()
  })
})
