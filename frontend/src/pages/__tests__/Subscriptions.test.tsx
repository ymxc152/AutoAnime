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
import { strings } from '../../strings'
import { mockCalendarItems, mockSubscriptions } from '../../mocks/data'
import type { SeasonBrowseOut, SeasonName, SubscriptionDto } from '../../api/types'
import { resetMockState } from '../../mocks/handlers'

/** 与页面同规则:当前月 → 默认季名(用于断言 season-browse 查询参数,避免跨月脆弱) */
function expectedDefaultSeason(): SeasonName {
  const month = new Date().getMonth()
  if (month >= 3 && month <= 5) return 'spring'
  if (month >= 6 && month <= 8) return 'summer'
  if (month >= 9) return 'fall'
  return 'winter'
}

/** 与页面默认过滤档一致:有中文翻译 且 平台非 OVA/ONA 等特别篇 */
function visibleByDefault(): typeof mockCalendarItems {
  return mockCalendarItems.filter(
    (it) => it.title_cn !== null && !/ova|ona|mad|剧场版|电影/i.test(it.platform ?? ''),
  )
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
    // 过滤偏好会写 localStorage(同文件用例共享),逐用例清掉防串扰
    localStorage.removeItem('autoanime-calendar-filters')
  })

  afterEach(() => {
    // 防个别用例的 api spy 未 restore 污染后续用例
    vi.restoreAllMocks()
  })

  // ---------- 12-IA:Tab1 季度选番 ----------

  it('12-IA:默认落在「季度选番」Tab,当季网格渲染 mock 条目(评分/集数/卡片数)', async () => {
    renderPage(<SubscriptionsPage />)
    // 默认选中:季度选番 aria-selected=true
    expect(screen.getByRole('tab', { name: '季度选番' })).toHaveAttribute('aria-selected', 'true')
    const grid = await screen.findByTestId('season-grid')
    expect(within(grid).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(visibleByDefault().length)
    expect(screen.getByText('孤独摇滚')).toBeInTheDocument()
    // 评分与集数(mock 唯一 13 集条目 = 魔法使いの夜 7.9 分);12-UX:评分为实心 Star 图标 + 文本,不再是方块 mark 徽标
    const rating = screen.getByText('7.9 分')
    expect(rating.previousElementSibling?.tagName.toLowerCase()).toBe('svg')
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
    await user.selectOptions(
      screen.getByTestId('season-select'),
      `2025:${expectedDefaultSeason()}`,
    )
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
    await user.selectOptions(screen.getByTestId('season-select'), '2024:winter')
    const hint = await screen.findByTestId('season-degraded')
    expect(hint).toHaveTextContent('该季度数据暂时不可用')
  })

  it('12-UX:选番顶部搜索框按标题过滤卡片,无命中显示专门空态', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    const grid = await screen.findByTestId('season-grid')
    expect(within(grid).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(visibleByDefault().length)
    // 命中:mock 中「孤独摇滚」唯一
    await user.type(screen.getByRole('searchbox', { name: '搜索本季番剧…' }), '孤独摇滚')
    await waitFor(() =>
      expect(within(screen.getByTestId('season-grid')).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(1),
    )
    expect(screen.getByText('孤独摇滚')).toBeInTheDocument()
    // 清空恢复
    await user.clear(screen.getByRole('searchbox', { name: '搜索本季番剧…' }))
    await waitFor(() =>
      expect(within(screen.getByTestId('season-grid')).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(visibleByDefault().length),
    )
    // 无命中:专门空态(区别于"该季度暂无条目")
    await user.type(screen.getByRole('searchbox', { name: '搜索本季番剧…' }), '不存在的番剧名')
    expect(await screen.findByText('没有匹配的番剧')).toBeInTheDocument()
    expect(screen.queryByTestId('season-grid')).not.toBeInTheDocument()
  })

  it('12-UX:默认过滤特别篇与无中文番剧,开关可显示,计数行如实', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await screen.findByTestId('season-grid')
    // 默认:511102(无中文)与 511104(OVA)被隐藏;计数 共 5 部 · 已隐藏 2 部
    expect(screen.queryByTestId('anime-card-511102')).not.toBeInTheDocument()
    expect(screen.queryByTestId('anime-card-511104')).not.toBeInTheDocument()
    expect(screen.getByText('共 5 部')).toBeInTheDocument()
    expect(screen.getByText(/已隐藏 2 部/)).toBeInTheDocument()
    // 显示特别篇 → 511104(OVA)出现且带平台徽标
    await user.click(screen.getByTestId('toggle-specials'))
    expect(screen.getByTestId('anime-card-511104')).toBeInTheDocument()
    expect(within(screen.getByTestId('anime-card-511104')).getByText('OVA')).toBeInTheDocument()
    // 显示无中文翻译 → 511102 出现
    await user.click(screen.getByTestId('toggle-nocn'))
    expect(screen.getByTestId('anime-card-511102')).toBeInTheDocument()
    expect(screen.getByText('共 7 部')).toBeInTheDocument()
    // 过滤偏好持久化到 localStorage
    const saved = JSON.parse(localStorage.getItem('autoanime-calendar-filters') ?? '{}')
    expect(saved.showSpecials).toBe(true)
    expect(saved.showNoCn).toBe(true)
  })

  it('12-UX:地区标签筛选(国漫只显示 region=cn 条目)', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await screen.findByTestId('season-grid')
    await user.click(screen.getByTestId('region-chip-cn'))
    const grid = screen.getByTestId('season-grid')
    expect(within(grid).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(1)
    expect(screen.getByTestId('anime-card-511106')).toBeInTheDocument()
    expect(screen.getByText('时光代理人')).toBeInTheDocument()
    expect(screen.getByText('共 1 部')).toBeInTheDocument()
    // 回全部恢复
    await user.click(screen.getByTestId('region-chip-all'))
    expect(within(screen.getByTestId('season-grid')).getAllByTestId(/^anime-card-\d+$/)).toHaveLength(
      visibleByDefault().length,
    )
  })

  it('12-UX:抽屉填通用 RSS 后可跑匹配预览(将下载/备选/已排除),创建带规则', async () => {
    const user = userEvent.setup()
    const previewSpy = vi.spyOn(api.pipeline, 'rssPreview')
    const createSpy = vi.spyOn(api.subscriptions, 'create')
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    await user.type(
      within(dialog).getByLabelText('RSS'),
      'https://rss.example.com/api/rss/fetch?pageSize=50',
    )
    await user.type(within(dialog).getByLabelText('必须排除关键词'), '内嵌广告')
    await user.click(within(dialog).getByRole('button', { name: '匹配预览' }))
    await waitFor(() => expect(previewSpy).toHaveBeenCalledTimes(1))
    const previewBody = previewSpy.mock.calls[0]![0]
    expect(previewBody.rss_url).toContain('rss.example.com')
    expect(previewBody.exclude_keywords).toBe('内嵌广告')
    const box = await within(dialog).findByTestId('rss-preview')
    expect(within(box).getByText(/4 条中 1 条会被下载/)).toBeInTheDocument()
    expect(within(box).getAllByText(/将下载/).length).toBe(1)
    expect(within(box).getAllByText(/备选/).length).toBeGreaterThan(0)
    expect(within(box).getAllByText(/已排除/).length).toBe(2)
    await user.click(within(dialog).getByRole('button', { name: '订阅并挂 RSS' }))
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1))
    expect(createSpy.mock.calls[0]![0].exclude_keywords).toBe('内嵌广告')
  })

  it('12-UX:编辑抽屉可更新 include/exclude 规则', async () => {
    const user = userEvent.setup()
    renderPage(<SubscriptionsPage />)
    await openMineTab(user)
    const row = (await screen.findByText('药屋少女的呢喃')).closest(
      'div.border-b',
    ) as HTMLElement
    await user.click(within(row).getByRole('button', { name: '编辑' }))
    const dialog = screen.getByRole('dialog')
    const updateSpy = vi.spyOn(api.subscriptions, 'update')
    await user.type(within(dialog).getByLabelText('必须包含关键词'), '简中;B-Global')
    await user.click(within(dialog).getByRole('button', { name: strings.common.save }))
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1))
    expect(updateSpy.mock.calls[0]![1].include_keywords).toBe('简中;B-Global')
  })

  it('12-UX:已在订阅中的番剧,选番卡片显示「已订阅」角标', async () => {
    vi.spyOn(api.subscriptions, 'list').mockResolvedValue({
      items: [{ ...mockSubscriptions[0]!, title_cn: '孤独摇滚', title_jp: 'ぼっち・ざ・ろっく!' }],
      total: 1,
      limit: 100,
      offset: 0,
    })
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    expect(await screen.findByTestId('anime-card-subscribed-511100')).toBeInTheDocument()
    expect(within(card).getByText('已订阅')).toBeInTheDocument()
    // 未订阅的卡片无角标
    expect(screen.queryByTestId('anime-card-subscribed-511101')).not.toBeInTheDocument()
  })

  it('12-UX:订阅抽屉自动获取 Mikan 字幕组,点选即填 RSS(失败可重试/可手动)', async () => {
    const user = userEvent.setup()
    const spy = vi.spyOn(api.mikan, 'subtitleGroups')
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511100')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    // 打开抽屉即拉取(mock 返回 LoliHouse/喵萌奶茶屋 两个选项)
    await waitFor(() => expect(spy).toHaveBeenCalledWith('孤独摇滚'))
    const box = within(dialog).getByTestId('mikan-groups')
    const groupBtn = await within(box).findByRole('button', { name: /喵萌奶茶屋/ })
    // 点选后:RSS 字段被填入对应地址,主按钮可用,字幕组偏好顺带填上
    await user.click(groupBtn)
    expect(within(dialog).getByLabelText('RSS')).toHaveValue(
      'https://mikanani.me/RSS/Bangumi?bangumiId=3281&subgroupid=615',
    )
    expect(within(dialog).getByLabelText('字幕组偏好')).toHaveValue('喵萌奶茶屋')
    expect(within(dialog).getByRole('button', { name: '订阅并挂 RSS' })).toBeEnabled()
    expect(within(box).getByText('已选:喵萌奶茶屋')).toBeInTheDocument()
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
    // create 有 120ms mock 延迟,toast 在 resolve 之后触发;仅订阅路径用专属引导文案
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith(
        '订阅成功。可在「我的订阅」顶部「管理 RSS 源」挂上 RSS 后自动下载',
      ),
    )
    successSpy.mockRestore()
  })

  it('12-IA:重复订阅 adopted=true → toast 提示已合并到现有条目', async () => {
    const user = userEvent.setup()
    const successSpy = vi.spyOn(toast, 'success')
    // 直接用 SubscriptionDto 类型字面量(rss_saved/adopted 为可选回显字段),不再 as never 展开
    const adoptedSub: SubscriptionDto = {
      id: 9,
      title_cn: '孤独摇滚',
      title_jp: null,
      title_romaji: null,
      media_type: 'tv',
      status: 'active',
      fansub_pref: null,
      quality_pref: null,
      include_keywords: null,
      exclude_keywords: null,
      seasons: [],
      rss_saved: false,
      adopted: true,
    }
    const createSpy = vi.spyOn(api.subscriptions, 'create').mockResolvedValueOnce(adoptedSub)
    renderPage(<SubscriptionsPage />)
    const card = await screen.findByTestId('anime-card-511103')
    await user.click(within(card).getByRole('button'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '仅订阅(RSS 稍后再挂)' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('已存在同名订阅,已合并到现有条目'),
    )
    createSpy.mockRestore()
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
    // 12-UX 口径修复只影响「全部季未放送」的订阅:mock 订阅已有放送内容,徽章仍显示「连载中」
    expect(screen.getAllByText('连载中').length).toBeGreaterThan(0)
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
    // P1-UX:mock 预生成季 status=upcoming → 行内如实显示「尚未放送」,不再显示「已归档 0/12 集」
    expect(await screen.findByText('尚未放送')).toBeInTheDocument()
    expect(screen.queryByText('已归档 0/12 集')).not.toBeInTheDocument()
    // 12-UX 口径修复:新订 active 且唯一季未放送 → 卡片徽章与季行同为「未放送」,不得宣称「连载中」
    const newRow = (await screen.findByText('测试番')).closest('div.border-b') as HTMLElement
    expect(within(newRow).getAllByText('未放送').length).toBeGreaterThanOrEqual(2)
    expect(within(newRow).queryByText('连载中')).not.toBeInTheDocument()
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
      include_keywords: null,
      exclude_keywords: null,
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
    // 12-UX:校验错误文字 text-sm font-medium(深色模式可读),不再用 text-xs
    expect(alert).toHaveClass('text-sm', 'font-medium', 'text-danger')
  })
})
