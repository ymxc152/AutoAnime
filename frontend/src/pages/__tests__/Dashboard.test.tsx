/*
 * Dashboard 渲染冒烟:指标卡(介入率/待确认/LLM 调用率)+ 三级统计 + 周曲线 + 集状态分布。
 */
import { screen, waitFor } from '@testing-library/react'
import { DashboardPage } from '../Dashboard'
import { renderPage } from '../../test/testUtils'
import { resetMockState, setMockMetrics } from '../../mocks/handlers'
import { mockMetrics } from '../../mocks/data'

describe('DashboardPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染三个核心指标卡(人工介入率/待确认队列/LLM 调用率)', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByText('人工介入率')).toBeInTheDocument()
    expect(screen.getByText('待确认队列')).toBeInTheDocument()
    expect(screen.getByText('LLM 调用率')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('4.8%')).toBeInTheDocument()
    })
    // 待确认卡:值 4 + 单位 hint(值 "4" 在周曲线里也出现,用 hint 消歧)
    expect(screen.getByText('条待人工确认')).toBeInTheDocument()
    // 全级别汇总 31/431(唯一 hint 文本)
    expect(screen.getByText('31 / 431')).toBeInTheDocument()
    expect(screen.getByText('7.2%')).toBeInTheDocument()
  })

  it('渲染三级管线统计(各级解析数)', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByText('L1 本地解析')).toBeInTheDocument()
    expect(screen.getByText('L2 记忆命中')).toBeInTheDocument()
    expect(screen.getByText('L3 LLM 兜底')).toBeInTheDocument()
    expect(screen.getByText('291')).toBeInTheDocument()
    expect(screen.getByText('96')).toBeInTheDocument()
  })

  it('渲染 LLM 调用周曲线(SVG)与库内集状态分布', async () => {
    renderPage(<DashboardPage />)
    expect(
      await screen.findByRole('img', { name: 'LLM 调用周曲线' }),
    ).toBeInTheDocument()
    expect(screen.getByText('库内集状态分布')).toBeInTheDocument()
    // episode_states 徽标
    expect(screen.getByText(/missing 30/)).toBeInTheDocument()
    expect(screen.getByText(/organized 87/)).toBeInTheDocument()
  })

  it('周曲线过滤空桶(0 调用的周不渲染)', async () => {
    renderPage(<DashboardPage />)
    const svg = await screen.findByRole('img', { name: 'LLM 调用周曲线' })
    // mock 数据 W35/W36 两周 llm_called=0,应被过滤
    expect(svg.textContent).not.toContain('W35')
    expect(svg.textContent).not.toContain('W36')
    expect(svg.textContent).toContain('W29')
  })

  it('周曲线全为 0 时显示空态', async () => {
    setMockMetrics({
      ...mockMetrics,
      llm_call_curve_weekly: mockMetrics.llm_call_curve_weekly.map((p) => ({
        ...p,
        llm_called: 0,
      })),
    })
    renderPage(<DashboardPage />)
    expect(await screen.findByText('暂无数据')).toBeInTheDocument()
  })
})
