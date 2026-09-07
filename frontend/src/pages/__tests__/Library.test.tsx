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
})

