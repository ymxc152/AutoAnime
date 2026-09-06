/*
 * Subscriptions 冒烟 + 交互(对齐后端 SubscriptionOut/SubscriptionCreateIn):
 * series 载体 + 每季进度;POST 至少一个标题 + 预生成集表;RSS 关联走「RSS 源」页。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SubscriptionsPage } from '../Subscriptions'
import { renderPage } from '../../test/testUtils'
import { api, ApiError } from '../../api'
import { resetMockState } from '../../mocks/handlers'

describe('SubscriptionsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染订阅列表 + 每季进度(已归档/缺集/RSS 源数)', async () => {
    renderPage(<SubscriptionsPage />)
    expect(await screen.findByText('药屋少女的呢喃')).toBeInTheDocument()
    expect(screen.getByText('已归档 15/24 集')).toBeInTheDocument()
    expect(screen.getByText('缺 8 集')).toBeInTheDocument()
    // 药屋与迷宫饭各挂 1 条 RSS 源
    expect(screen.getAllByText('RSS 源 1').length).toBe(2)
    expect(screen.getByText('迷宫饭')).toBeInTheDocument()
  })

  it('完结收藏的订阅显示已收藏状态标', async () => {
    renderPage(<SubscriptionsPage />)
    const row = (await screen.findByText('葬送的芙莉莲')).closest<HTMLElement>('div.border-b')!
    expect(within(row).getAllByText('已收藏').length).toBeGreaterThan(0)
    expect(within(row).getByText('已归档 28/28 集')).toBeInTheDocument()
  })

  it('Mikan 选番入口与 RSS 关联提示存在', async () => {
    renderPage(<SubscriptionsPage />)
    expect(await screen.findByText(/先在这里建订阅,再在「RSS 源」页/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /去 Mikan 选番/ })
    expect(link).toHaveAttribute('href', 'https://mikanani.me')
  })

  it('取消订阅:点移除 → 确认条出现 → 确认后行消失', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    const row = (await screen.findByText('迷宫饭')).closest('div')!
    await user.click(within(row).getByRole('button', { name: '移除' }))
    expect(await screen.findByText(/确认取消订阅「迷宫饭」/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await waitFor(() => expect(screen.queryByText('迷宫饭')).not.toBeInTheDocument())
  })

  it('回归 A2:取消订阅失败不再静默,展示 role=alert 错误条且行保留,重试可恢复', async () => {
    const user = userEvent.setup()
    const removeSpy = vi
      .spyOn(api.subscriptions, 'remove')
      .mockRejectedValueOnce(new ApiError(500, 'db locked'))
    renderPage(<SubscriptionsPage />)
    const row = (await screen.findByText('迷宫饭')).closest('div')!
    await user.click(within(row).getByRole('button', { name: '移除' }))
    await user.click(await screen.findByRole('button', { name: '确认' }))
    // 失败信息如实展示(操作失败 + 后端 detail),行未被误删
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('操作失败')
    expect(alert).toHaveTextContent('db locked')
    expect(screen.getByText('迷宫饭')).toBeInTheDocument()
    // 恢复后重试:错误条消失,行移除
    removeSpy.mockRestore()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await waitFor(() => expect(screen.queryByText('迷宫饭')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('添加订阅:标题 + 季号 + 集数(预生成 MISSING 集表)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await user.type(await screen.findByLabelText('标题(至少填一个语言的标题)'), '测试番')
    await user.type(screen.getByLabelText('当季集数(可选)'), '12')
    await user.click(screen.getByRole('button', { name: '订阅' }))
    expect(await screen.findByText('测试番')).toBeInTheDocument()
    // 预生成集表:全 MISSING → 已归档 0/12
    expect(await screen.findByText('已归档 0/12 集')).toBeInTheDocument()
  })

  it('空标题提交显示校验错误(对齐后端至少一个标题)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await screen.findByText('药屋少女的呢喃')
    await user.click(screen.getByRole('button', { name: '订阅' }))
    expect(await screen.findByText('请填写标题')).toBeInTheDocument()
  })
})
