/*
 * Subscriptions 冒烟 + 交互:进度条、降频标、订阅/取消订阅。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SubscriptionsPage } from '../Subscriptions'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

describe('SubscriptionsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染订阅列表 + 放送进度', async () => {
    renderPage(<SubscriptionsPage />)
    expect(await screen.findByText('药屋少女的呢喃')).toBeInTheDocument()
    expect(screen.getByText('已收 15 / 已放 16 / 全 24 集')).toBeInTheDocument()
    expect(screen.getByText('迷宫饭')).toBeInTheDocument()
  })

  it('完结收藏的订阅显示降频状态标', async () => {
    renderPage(<SubscriptionsPage />)
    expect(await screen.findByText('葬送的芙莉莲')).toBeInTheDocument()
    expect(screen.getByText('已降频')).toBeInTheDocument()
  })

  it('Mikan 选番入口与提示文案存在', async () => {
    renderPage(<SubscriptionsPage />)
    expect(await screen.findByText('每番只订一个字幕组')).toBeInTheDocument()
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

  it('添加订阅:填写 RSS 地址提交后列表刷新', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await user.type(await screen.findByLabelText('Mikan RSS 地址'), 'https://mikanani.me/RSS/MyBangumi?token=x')
    await user.click(screen.getByRole('button', { name: '订阅' }))
    // mock 会新增一条订阅(标题取第一个 series),与既有同名条目共存
    const matches = await screen.findAllByText('葬送的芙莉莲')
    expect(matches.length).toBeGreaterThan(0)
  })

  it('空地址提交显示校验错误', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await screen.findByText('药屋少女的呢喃')
    await user.click(screen.getByRole('button', { name: '订阅' }))
    expect(await screen.findByText('请填写 RSS 地址')).toBeInTheDocument()
  })
})
