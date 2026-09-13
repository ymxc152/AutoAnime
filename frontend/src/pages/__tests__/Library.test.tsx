/*
 * Library 冒烟 + 交互:卡片网格、搜索过滤、明细抽屉(季切换)、
 * 集行「重新识别」两步契约(12-F:dry-run 预览 → 确认执行)。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { LibraryPage } from '../Library'
import { renderPage } from '../../test/testUtils'
import { api } from '../../api'
import type { EpisodeReparseOut } from '../../api/types'
import { resetMockState } from '../../mocks/handlers'

describe('LibraryPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染 series 卡片网格', async () => {
    renderPage(<LibraryPage />)
    expect(await screen.findByText('葬送的芙莉莲')).toBeInTheDocument()
    expect(screen.getByText('药屋少女的呢喃')).toBeInTheDocument()
    expect(screen.getByText('剧场版 声之形')).toBeInTheDocument()
  })

  it('卡片渲染海报 <img>(指向后端本地库代理端点)', async () => {
    renderPage(<LibraryPage />)
    await screen.findByText('葬送的芙莉莲')
    const imgs = screen.getAllByAltText('') as HTMLImageElement[]
    expect(imgs.length).toBeGreaterThan(0)
    const poster = imgs[0] as HTMLImageElement
    expect(poster.getAttribute('src')).toBe('/api/series/1/poster')
    expect(poster.getAttribute('loading')).toBe('lazy')
  })

  it('海报加载失败时降级为首字占位块(404 无海报与 401 token 未授权同路:onError 统一降级)', async () => {
    renderPage(<LibraryPage />)
    await screen.findByText('葬送的芙莉莲')
    const img = screen.getAllByAltText('')[0] as HTMLImageElement
    fireEvent.error(img)
    // 失败的那张 img 被占位块替换(标题首字),其余卡片海报不受影响
    expect(screen.getByText('葬')).toBeInTheDocument()
    expect(screen.getAllByAltText('').length).toBeLessThan(6)
  })

  it('搜索过滤标题', async () => {
    const user = userEvent.setup()
    renderPage(<LibraryPage />)
    await screen.findByText('葬送的芙莉莲')
    await user.type(screen.getByRole('searchbox'), '药屋')
    // mock 拉取有延迟,等过滤结果落地
    await waitFor(() => {
      expect(screen.queryByText('葬送的芙莉莲')).not.toBeInTheDocument()
    })
    expect(screen.getByText('药屋少女的呢喃')).toBeInTheDocument()
  })

  it('点开抽屉查看季/集明细与 quality_score 徽标', async () => {
    const user = userEvent.setup()
    renderPage(<LibraryPage />)
    await user.click(await screen.findByText('葬送的芙莉莲'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getAllByText(/第 1 季/).length).toBeGreaterThan(0)
    // 第一季 28 集,有洗版集(quality 11)
    expect(within(dialog).getAllByText(/E01/).length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('11.0').length).toBeGreaterThan(0)
  })

  it('抽屉内切换季', async () => {
    const user = userEvent.setup()
    renderPage(<LibraryPage />)
    await user.click(await screen.findByText('我推的孩子'))
    const dialog = await screen.findByRole('dialog')
    // 默认第 1 季;切到第 2 季
    await user.click(within(dialog).getByText(/第 2 季/))
    expect(within(dialog).getAllByText(/E04/).length).toBeGreaterThan(0)
  })

  it('uxfix:空态去重 —— 保留标题+一句成因+去追番 CTA,重复句不再渲染;计数在搜索框同行工具栏', async () => {
    vi.spyOn(api.series, 'list').mockResolvedValueOnce({
      total: 0,
      limit: 24,
      offset: 0,
      items: [],
    })
    renderPage(<LibraryPage />)
    // 保留:strings.library.empty(标题 + 一句成因)
    expect(
      await screen.findByText('媒体库为空。添加订阅后,归档的剧集会出现在这里。'),
    ).toBeInTheDocument()
    // 去重:原 description(uxfix.emptyLibraryHint)与标题句重复,不再渲染
    expect(
      screen.queryByText('添加订阅并完成导入后,归档的剧集会出现在这里'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/每番只订一个字幕组/)).not.toBeInTheDocument()
    // 空态卡下方的直达链接指向追番页
    expect(screen.getByRole('link', { name: '去「追番」创建订阅' })).toHaveAttribute(
      'href',
      '/subscriptions',
    )
    // 计数归位:空态时「共 0 条」也在搜索框同行工具栏(行为与有数据时一致)
    const toolbar = screen.getByRole('searchbox').parentElement as HTMLElement
    expect(within(toolbar).getByText('共 0 条')).toBeInTheDocument()
  })

  it('uxfix:总数计数显示在搜索框同行工具栏右侧,不再孤挂网格下方', async () => {
    renderPage(<LibraryPage />)
    await screen.findByText('葬送的芙莉莲')
    const toolbar = screen.getByRole('searchbox').parentElement as HTMLElement
    expect(within(toolbar).getByText('共 6 条')).toBeInTheDocument()
  })

  it('12-F:重新识别 —— 先 dry-run 预览展示解析结果与目标路径,确认执行二次确认后移动', async () => {
    // ConfirmHost 未挂载时 confirmDialog 退回 window.confirm,这里确认放行
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const successSpy = vi.spyOn(toast, 'success')
    const preview: EpisodeReparseOut = {
      episode_id: 101,
      dry_run: true,
      parsed: {
        title: '葬送的芙莉莲', season: 1, episode: 1, segment: 'episode', fansub: null,
        level: 'high', confidence: 1, missing_fields: [], evidence: {},
      },
      action: {
        dst: '/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E01.1080p.mkv',
        strategy: 'hardlink',
        episode_state: 'organized',
        action: 'archive',
      },
    }
    const executed: EpisodeReparseOut = { ...preview, dry_run: false }
    const reparseSpy = vi
      .spyOn(api.episodes, 'reparse')
      .mockImplementation(async (_id, body) => (body.dry_run ? preview : executed))
    const user = userEvent.setup()
    renderPage(<LibraryPage />)
    await user.click(await screen.findByText('葬送的芙莉莲'))
    const drawer = await screen.findByRole('dialog')
    // 第一步:点击行内「重新识别」→ dry_run=true 拉预览
    await user.click(within(drawer).getAllByRole('button', { name: /重新识别/ })[0]!)
    await waitFor(() => expect(reparseSpy).toHaveBeenCalledWith(101, { dry_run: true }))
    // 预览抽屉展示「解析结果 → 将执行的动作」(原文件/识别标题/季集/目标路径)
    const previewDrawer = (await screen.findAllByRole('dialog')).at(-1)!
    expect(within(previewDrawer).getByText('葬送的芙莉莲 - S01E01.1080p.mkv')).toBeInTheDocument()
    expect(within(previewDrawer).getByText('目标路径')).toBeInTheDocument()
    expect(
      within(previewDrawer).getByText('/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E01.1080p.mkv'),
    ).toBeInTheDocument()
    // 第二步:danger「确认执行」→ confirmDialog 二次确认 → dry_run=false
    await user.click(within(previewDrawer).getByRole('button', { name: '确认执行' }))
    await waitFor(() => expect(reparseSpy).toHaveBeenCalledWith(101, { dry_run: false }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith(
        '重新识别完成,已归档到 /library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E01.1080p.mkv',
      ),
    )
    reparseSpy.mockRestore()
    successSpy.mockRestore()
  })

  it('12-F:重新识别预览后可取消,不执行移动', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const preview: EpisodeReparseOut = {
      episode_id: 101,
      dry_run: true,
      parsed: null,
      action: {
        dst: '/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E01.1080p.mkv',
        strategy: 'hardlink',
        episode_state: 'organized',
        action: 'archive',
      },
    }
    const reparseSpy = vi.spyOn(api.episodes, 'reparse').mockResolvedValue(preview)
    const user = userEvent.setup()
    renderPage(<LibraryPage />)
    await user.click(await screen.findByText('葬送的芙莉莲'))
    const drawer = await screen.findByRole('dialog')
    await user.click(within(drawer).getAllByRole('button', { name: /重新识别/ })[0]!)
    const previewDrawer = (await screen.findAllByRole('dialog')).at(-1)!
    // parsed 为 null 时如实提示,不编造识别结果
    expect(await within(previewDrawer).findByText(/未能解析出识别结果/)).toBeInTheDocument()
    // 取消:不再发起第二步调用
    await user.click(within(previewDrawer).getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.getAllByRole('dialog').length).toBe(1))
    expect(reparseSpy).toHaveBeenCalledTimes(1)
    reparseSpy.mockRestore()
  })

  it('重新识别预览进行中:所有行的「重新识别」按钮禁用(而非静默 no-op),预览弹出后恢复', async () => {
    const preview: EpisodeReparseOut = {
      episode_id: 101,
      dry_run: true,
      parsed: null,
      action: {
        dst: '/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E01.1080p.mkv',
        strategy: 'hardlink',
        episode_state: 'organized',
        action: 'archive',
      },
    }
    // 用受控 Promise 让 dry-run 预览停在在途,观察其它行按钮状态
    let resolvePreview: (value: EpisodeReparseOut) => void = () => {}
    vi.spyOn(api.episodes, 'reparse').mockImplementation(
      () => new Promise<EpisodeReparseOut>((resolve) => { resolvePreview = resolve }),
    )
    const user = userEvent.setup()
    renderPage(<LibraryPage />)
    await user.click(await screen.findByText('葬送的芙莉莲'))
    const drawer = await screen.findByRole('dialog')
    const reparseButtons = () => within(drawer).getAllByRole('button', { name: /重新识别/ })
    await user.click(reparseButtons()[0]!)
    // 预览在途:当前行 loading、其它行 disabled,全部不可点击
    await waitFor(() => {
      for (const button of reparseButtons()) {
        expect(button).toBeDisabled()
      }
    })
    // 预览返回并弹出预览抽屉后,行按钮恢复可用
    resolvePreview(preview)
    await waitFor(() => {
      for (const button of reparseButtons()) {
        expect(button).toBeEnabled()
      }
    })
    vi.restoreAllMocks()
  })
})

