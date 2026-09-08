/*
 * Subscriptions 冒烟 + 交互(P1-E 双 Tab 信息架构):
 * Tab1 季度选番(默认;season-calendar/season-browse + AnimeCard + SubscriptionDrawer);
 * Tab2 我的订阅(既有列表/表单/编辑/删除功能整体迁入,回归用例先切 Tab)。
 * 对齐后端 SubscriptionCreateIn(P0-B:bangumi_id/rss_url/rss_token)与 SeasonBrowseOut。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { toast } from 'sonner'
import { SubscriptionsPage } from '../Subscriptions'
import { renderPage } from '../../test/testUtils'
import { api, ApiError } from '../../api'
import { mockCalendarItems } from '../../mocks/data'
import type { SeasonBrowseOut, SeasonName } from '../../api/types'
import { resetMockState } from '../../mocks/handlers'

/** 与页面同规则:当前月 → 默认季名(用于断言 season-browse 查询参数,避免跨月脆弱) */
function expectedDefaultSeason(): SeasonName {
  const month = new Date().getMonth()
  if (month >= 3 && month <= 5) return 'spring'
  if (month >= 6 && month <= 8) return 'summer'
  if (month >= 9) return 'fall'
  return 'winter'
}

/** 切到「我的订阅」Tab(既有用例迁移入口) */
async function openMineTab(user: UserEvent): Promise<void> {
  await user.click(await screen.findByRole('tab', { name: '我的订阅' }))
}

function browseOut(overrides: Partial<SeasonBrowseOut> = {}): SeasonBrowseOut {
  return { items: mockCalendarItems, degraded: false, reason: null, ...overrides }
}

describe('SubscriptionsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  // ---------- 12-IA:Tab1 季度选番 ----------

  it('12-IA:默认落在「季度选番」Tab,当季网格渲染 mock 条目(评分/集数/卡片数)', async () => {
    renderPage(<SubscriptionsPage />)
    // 默认选中:季度选番 aria-selected=true
    expect(screen.getByRole('tab', { name: '季度选番' })).toHaveAttribute('aria-selected', 'true')
    const grid = await screen.findByTestId('season-grid')
    expect(within(grid).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(mockCalendarItems.length)
    expect(screen.getByText('孤独摇滚')).toBeInTheDocument()
    // 评分 Badge 与集数(mock 唯一 13 集条目 = 魔法使いの夜 7.9 分)
    expect(screen.getByText('7.9 分')).toBeInTheDocument()
    expect(screen.getByText('13 集')).toBeInTheDocument()
    // 网格不串台:Tab2 的订阅列表此时不渲染
    expect(screen.queryByText('药屋少女的呢喃')).not.toBeInTheDocument()
  })

  it('12-IA:每张卡片带「在 Mikan 搜索」外链(target=_blank,不冒泡触发抽屉)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    const link = within(card).getByTestId('anime-card-mikan-link')
    expect(link).toHaveAttribute('href', expect.stringContaining('https://mikanani.me/'))
    expect(link).toHaveAttribute('target', '_blank')
    // 点击外链不开抽屉(兄弟节点,天然不冒泡)
    await user.click(link)
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    )
  })

  it('12-IA:切历史季走 GET season-browse(年份×季节),回当季走 season-calendar', async () => {
    const user = userEvent.setup()
    const browseSpy = vi.spyOn(api.seasonBrowse, 'get').mockResolvedValue(browseOut())
    const calendarSpy = vi.spyOn(api.seasonCalendar, 'get').mockResolvedValue(browseOut())
    renderPage(<SubscriptionsPage />)
    await screen.findByTestId('season-grid')
    await user.selectOptions(screen.getByTestId('season-year-select'), '2025')
    await waitFor(() =>
      expect(browseSpy).toHaveBeenCalledWith({ year: 2025, season: expectedDefaultSeason() }),
    )
    // 回当季:calendar 再被调用
    await user.click(screen.getByRole('button', { name: '当季' }))
    await waitFor(() => expect(calendarSpy.mock.calls.length).toBeGreaterThanOrEqual(2))
    browseSpy.mockRestore()
    calendarSpy.mockRestore()
  })

  it('12-IA:degraded=true 时显示降级提示行(不伪装成空网格)', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.seasonBrowse, 'get').mockResolvedValue(
      browseOut({ items: [], degraded: true, reason: 'bangumi unreachable' }),
    )
    renderPage(<SubscriptionsPage />)
    await screen.findByTestId('season-grid')
    await user.selectOptions(screen.getByTestId('season-year-select'), '2024')
    const hint = await screen.findByTestId('season-degraded')
    expect(hint).toHaveTextContent('该历史季度暂不可用')
  })

  it('12-IA:点卡片打开订阅抽屉(标题/详情行/三个表单字段)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    // 标题在抽屉头部(subtitle)与信息块各出现一次
    expect(within(dialog).getAllByText('孤独摇滚').length).toBeGreaterThan(0)
    expect(within(dialog).getByLabelText('字幕组偏好')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('RSS')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('令牌(可选)')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '订阅并挂 RSS' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: '仅订阅(RSS 稍后再挂)' })).toBeEnabled()
  })

  it('12-IA:「订阅并挂 RSS」单次 POST,body 含 bangumi_id/rss_url,成功后切到我的订阅', async () => {
    const user = userEvent.setup()
    const successSpy = vi.spyOn(toast, 'success')
    const createSpy = vi.spyOn(api.subscriptions, 'create')
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    // 填 RSS 后主按钮可用
    await user.type(within(dialog).getByLabelText('RSS'), 'https://mikanani.me/RSS/Bangumi?subgroupid=583')
    expect(within(dialog).getByRole('button', { name: '订阅并挂 RSS' })).toBeEnabled()
    await user.click(within(dialog).getByRole('button', { name: '订阅并挂 RSS' }))
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1))
    const body = createSpy.mock.calls[0]![0]
    expect(body.bangumi_id).toBe('511100')
    expect(body.title_cn).toBe('孤独摇滚')
    expect(body.rss_url).toBe('https://mikanani.me/RSS/Bangumi?subgroupid=583')
    expect(body.episode_count).toBe(12)
    // rss_saved=true(mock 回显)→ 成功 toast,不误报 RSS 未挂成功
    await waitFor(() => expect(successSpy).toHaveBeenCalledWith('订阅成功,可去「媒体库」查看'))
    // 成功后自动切到「我的订阅」并渲染新订阅
    expect(await screen.findByRole('tab', { name: '我的订阅' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(await screen.findByText('孤独摇滚')).toBeInTheDocument()
    successSpy.mockRestore()
  })

  it('12-IA:「仅订阅」路径 rss_url 缺省,不出现在 body 中', async () => {
    const user = userEvent.setup()
    const successSpy = vi.spyOn(toast, 'success')
    const createSpy = vi.spyOn(api.subscriptions, 'create')
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511103')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '仅订阅(RSS 稍后再挂)' }))
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1))
    const body = createSpy.mock.calls[0]![0]
    expect(body.bangumi_id).toBe('511103')
    expect(body.rss_url).toBeUndefined()
    expect(body.rss_token).toBeUndefined()
    // eps=null 的条目不传 episode_count
    expect(body.episode_count).toBeUndefined()
    // create 有 120ms mock 延迟,toast 在 resolve 之后触发
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('订阅成功,可去「媒体库」查看'),
    )
    successSpy.mockRestore()
  })

  it('12-IA:订阅失败错误行内展示在抽屉内且不关闭', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.subscriptions, 'create').mockRejectedValueOnce(new ApiError(422, 'bad payload'))
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '仅订阅(RSS 稍后再挂)' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('bad payload')
    expect(dialog).toBeInTheDocument()
  })

  // ---------- Tab2 我的订阅:既有功能回归(迁移后全绿) ----------

  it('渲染订阅列表 + 每季进度(已归档/缺集/RSS 源数)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    expect(await screen.findByText('药屋少女的呢喃')).toBeInTheDocument()
    expect(screen.getByText('已归档 15/24 集')).toBeInTheDocument()
    expect(screen.getByText('缺 8 集')).toBeInTheDocument()
    // 药屋与迷宫饭各挂 1 条 RSS 源
    expect(screen.getAllByText('RSS 源 1').length).toBe(2)
    expect(screen.getByText('迷宫饭')).toBeInTheDocument()
  })

  it('12-IA:「我的订阅」Tab 顶部有「管理 RSS 源」链接(指向 /rss-sources)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    const link = await screen.findByTestId('manage-rss-link')
    expect(link).toHaveAttribute('href', '/rss-sources')
    expect(link).toHaveTextContent('管理 RSS 源')
  })

  it('完结收藏的订阅显示已收藏状态标', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    const row = (await screen.findByText('葬送的芙莉莲')).closest<HTMLElement>('div.border-b')!
    expect(within(row).getAllByText('已收藏').length).toBeGreaterThan(0)
    expect(within(row).getByText('已归档 28/28 集')).toBeInTheDocument()
  })

  it('Mikan 选番入口与 RSS 关联提示存在(12-IA 弹窗化:位于添加订阅弹窗内)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    await user.click(await screen.findByRole('button', { name: '添加订阅' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/先在这里建订阅,再在「RSS 源」页/)).toBeInTheDocument()
    const link = within(dialog).getByRole('link', { name: /去 Mikan 选番/ })
    expect(link).toHaveAttribute('href', 'https://mikanani.me')
  })

  it('取消订阅:点移除 → 确认条出现 → 确认后行消失', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
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
    await openMineTab(user)
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

  it('添加订阅:标题 + 季号 + 集数(预生成 MISSING 集表);成功后弹窗关闭(12-IA 弹窗化)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    await user.click(await screen.findByRole('button', { name: '添加订阅' }))
    await user.type(await screen.findByLabelText('标题(至少填一个语言的标题)'), '测试番')
    await user.type(screen.getByLabelText('当季集数(可选)'), '12')
    await user.click(screen.getByRole('button', { name: '订阅' }))
    expect(await screen.findByText('测试番')).toBeInTheDocument()
    // 预生成集表:全 MISSING → 已归档 0/12
    expect(await screen.findByText('已归档 0/12 集')).toBeInTheDocument()
    // 12-IA 弹窗化:提交成功后弹窗关闭
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('空标题提交显示校验错误(对齐后端至少一个标题)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    await user.click(await screen.findByRole('button', { name: '添加订阅' }))
    await screen.findByText('药屋少女的呢喃')
    await user.click(screen.getByRole('button', { name: '订阅' }))
    expect(await screen.findByText('请填写标题')).toBeInTheDocument()
  })

  it('12-IA 弹窗化:点「添加订阅」打开弹窗(标题/字段/提交按钮齐全)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    await user.click(await screen.findByRole('button', { name: '添加订阅' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: '添加订阅' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText('标题(至少填一个语言的标题)')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('季号')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('当季集数(可选)')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('字幕组偏好')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '订阅' })).toBeEnabled()
  })

  it('编辑订阅:状态/字幕组可更新,清空偏好显式提交 null', async () => {
    const user = userEvent.setup()
    const actualUpdate = api.subscriptions.update
    const updateSpy = vi.spyOn(api.subscriptions, 'update').mockImplementationOnce(async (...args) => actualUpdate(...args))
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    const row = (await screen.findByText('药屋少女的呢喃')).closest('div.border-b') as HTMLElement
    await user.click(within(row).getByRole('button', { name: '编辑' }))
    const dialog = screen.getByRole('dialog')
    await user.selectOptions(within(dialog).getByLabelText('状态'), 'paused')
    const fansub = within(dialog).getByPlaceholderText('如:Kamigakari')
    await user.clear(fansub)
    await user.type(fansub, 'LoliHouse')
    await user.clear(within(dialog).getByLabelText('质量偏好'))
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(updateSpy).toHaveBeenCalledWith(2, {
      status: 'paused',
      fansub_pref: 'LoliHouse',
      quality_pref: null,
    })
    // 断言收窄到行内:抽屉 select 的 option 也含「暂停」文案,全局查询会提前命中
    const updatedRow = (await screen.findByText('药屋少女的呢喃')).closest('div.border-b') as HTMLElement
    await waitFor(() => expect(within(updatedRow).getByText('暂停')).toBeInTheDocument())
    expect(within(updatedRow).getByText('LoliHouse')).toBeInTheDocument()
  })

  it('编辑订阅失败:错误展示在抽屉内且不关闭', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.subscriptions, 'update').mockRejectedValueOnce(new ApiError(409, 'status locked'))
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    const row = (await screen.findByText('药屋少女的呢喃')).closest('div.border-b') as HTMLElement
    await user.click(within(row).getByRole('button', { name: '编辑' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    const alert = await within(screen.getByRole('dialog')).findByRole('alert')
    expect(alert).toHaveTextContent('status locked')
  })
})
