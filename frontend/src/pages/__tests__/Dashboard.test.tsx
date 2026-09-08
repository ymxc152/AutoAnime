/*
 * Dashboard 渲染冒烟:指标卡(介入率/待确认/LLM 调用率)+ 三级统计 + 周曲线 + 集状态分布。
 */
import { screen, waitFor, within } from '@testing-library/react'
import { DashboardPage } from '../Dashboard'
import { renderPage } from '../../test/testUtils'
import { api } from '../../api'
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
    expect(screen.getByText(/缺集 30/)).toBeInTheDocument()
    expect(screen.getByText(/已归档 87/)).toBeInTheDocument()
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

  it('12-F:识别指标区块渲染 GET /api/report 的累计统计(与三级管线统计不重复)', async () => {
    renderPage(<DashboardPage />)
    expect(await screen.findByText('识别指标')).toBeInTheDocument()
    // 等 report 异步数据落地(mock 有 120ms 延迟)
    await screen.findByText('累计解析')
    // 限定在识别指标卡片内断言(页面上方指标卡/三级统计有重叠数值)
    const card = screen.getByText('识别指标').closest('section') as HTMLElement
    // 6 个精选字段:累计解析 / LLM 兜底 / LLM 调用率 / 归档事件 / 人工纠正 / 人工介入率
    expect(within(card).getByText('累计解析')).toBeInTheDocument()
    expect(within(card).getByText('LLM 兜底')).toBeInTheDocument()
    expect(within(card).getByText('LLM 调用率')).toBeInTheDocument()
    expect(within(card).getByText('归档事件')).toBeInTheDocument()
    expect(within(card).getByText('人工纠正')).toBeInTheDocument()
    expect(within(card).getByText('人工介入率')).toBeInTheDocument()
    // mock fixture 数值(parse_events.total=431 / llm_called_total=31 / rate=0.52%→0.5%)
    expect(within(card).getByText('431')).toBeInTheDocument()
    expect(within(card).getByText('31')).toBeInTheDocument()
    expect(within(card).getByText('7.2%')).toBeInTheDocument()
    expect(within(card).getByText('387')).toBeInTheDocument()
    expect(within(card).getByText('2')).toBeInTheDocument()
    expect(within(card).getByText('0.5%')).toBeInTheDocument()
  })

  it('12-F:识别指标加载失败时区块内展示错误,不影响其余指标卡', async () => {
    vi.spyOn(api.report, 'get').mockRejectedValueOnce(new Error('report unavailable'))
    renderPage(<DashboardPage />)
    expect(await screen.findByText(/识别指标加载失败/)).toBeInTheDocument()
    // 其余区块不受影响
    expect(screen.getByText('人工介入率')).toBeInTheDocument()
  })

  it('uxfix:订阅数为 0 时顶部显示三步引导卡,三个步骤分别跳转对应页面', async () => {
    // mockResolvedValue 非 Once:必须显式 restore,否则泄漏到下个用例(订阅恒空 → 引导卡恒显)
    const listSpy = vi.spyOn(api.subscriptions, 'list').mockResolvedValue({
      total: 0,
      limit: 1,
      offset: 0,
      items: [],
    })
    try {
      renderPage(<DashboardPage />)
      expect(await screen.findByText('三步开始使用')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /在「追番」页添加订阅/ })).toHaveAttribute(
        'href',
        '/subscriptions',
      )
      expect(screen.getByRole('link', { name: /在「RSS 源」页挂上 Mikan 订阅地址/ })).toHaveAttribute(
        'href',
        '/rss-sources',
      )
      expect(screen.getByRole('link', { name: /在「管线」页导入本地下载目录/ })).toHaveAttribute(
        'href',
        '/pipeline',
      )
    } finally {
      listSpy.mockRestore()
    }
  })

  it('uxfix:已有订阅时不显示三步引导卡', async () => {
    renderPage(<DashboardPage />)
    await screen.findByText('人工介入率')
    await waitFor(() => {
      expect(screen.queryByText('三步开始使用')).not.toBeInTheDocument()
    })
  })
})
