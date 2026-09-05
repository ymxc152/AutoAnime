/*
 * Dashboard 渲染冒烟:指标卡 + 三级命中 + 周曲线。
 */
import { screen, waitFor } from '@testing-library/react'
import { DashboardPage } from '../Dashboard'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

describe('DashboardPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染三个核心指标卡(人工介入率/本周归档/LLM 调用率)', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByText('人工介入率')).toBeInTheDocument()
    expect(screen.getByText('本周归档')).toBeInTheDocument()
    expect(screen.getByText('LLM 调用率')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('4.8%')).toBeInTheDocument()
    })
    expect(screen.getByText('26')).toBeInTheDocument()
    expect(screen.getByText('7.1%')).toBeInTheDocument()
  })

  it('渲染三级管线命中计数', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByText('L1 高置信')).toBeInTheDocument()
    expect(screen.getByText('L2 记忆命中')).toBeInTheDocument()
    expect(screen.getByText('L3 进入')).toBeInTheDocument()
    expect(screen.getByText('291')).toBeInTheDocument()
    expect(screen.getByText('96')).toBeInTheDocument()
  })

  it('渲染近 7 日曲线(SVG)', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByRole('img', { name: '近 7 日曲线' })).toBeInTheDocument()
  })
})
