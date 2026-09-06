/*
 * Library 冒烟 + 交互:卡片网格、搜索过滤、明细抽屉(季切换)。
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LibraryPage } from '../Library'
import { renderPage } from '../../test/testUtils'
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
})

