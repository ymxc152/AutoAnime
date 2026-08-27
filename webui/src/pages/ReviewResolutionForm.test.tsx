import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ReviewResolutionForm } from './ConsolePages'

afterEach(cleanup)

describe('ReviewResolutionForm', () => {
  it('submits a movie without season or episode fields', async () => {
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 1,
      payload: {
        title: '电影标题',
        season: 1,
        episode: 1,
        is_movie: true,
        release_tag: 'BDRip',
        source: 'D:\\downloads\\电影标题.BDRip.mkv',
        confidence: 0.8,
        evidence: [{ agent: 'parser', value: '电影标题', confidence: 0.8 }],
      },
    }} onSubmit={onSubmit} />)

    expect(screen.getByLabelText('媒体类型')).toHaveValue('movie')
    expect(screen.queryByLabelText('季度')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('集号')).not.toBeInTheDocument()
    expect(screen.getByText(/目标路径预览/).parentElement).toHaveTextContent('电影标题')
    // 来源文件与识别结果对照
    expect(screen.getByText('电影标题.BDRip.mkv')).toBeInTheDocument()
    expect(screen.getByText(/识别结果/).parentElement).toHaveTextContent('电影标题')
    expect(screen.getByText(/置信度 80%/)).toBeInTheDocument()
    // 识别依据列表展示了 agent，而非只有折叠 JSON
    expect(screen.getAllByText(/parser/).length).toBeGreaterThan(0)

    await userEvent.click(screen.getByRole('button', { name: '确认信息并生成计划' }))

    expect(onSubmit).toHaveBeenCalledWith({
      title: '电影标题',
      media_type: 'movie',
      release_tag: 'BDRip',
      manual_lock: true,
    })
  })

  it('renders clickable candidate chips, marks live-action, and fills the title on click', async () => {
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 3,
      payload: {
        title: '葬送的芙莉莲',
        season: 1,
        episode: 1,
        is_movie: false,
        release_tag: '',
        evidence: [],
        candidates: [
          { source: 'aiparse', lang: 'zh-cn', title: '葬送的芙莉莲' },
          { source: 'aiparse', lang: 'ja', title: '葬送のフリーレン' },
          { source: 'metadata', provider: 'bgm', provider_id: '7', title: '葬送的芙莉莲', confidence: 0.9, is_anime: true },
          { source: 'metadata', provider: 'tmdb', provider_id: '101', title: '葬送的芙莉蓮', confidence: 0.9, is_anime: false },
        ],
      },
    }} onSubmit={onSubmit} />)

    expect(screen.getByText(/候选标题/)).toBeInTheDocument()
    expect(screen.getByText('葬送のフリーレン')).toBeInTheDocument()
    expect(screen.getByText('AI·日文')).toBeInTheDocument()
    expect(screen.getByText('TMDB · 90%')).toBeInTheDocument()
    // 非动漫候选标记「真人版?」
    expect(screen.getByText('真人版?')).toBeInTheDocument()

    // 点击 ja 候选 → 标题填入
    await userEvent.click(screen.getByText('葬送のフリーレン'))
    expect(screen.getByLabelText('标题')).toHaveValue('葬送のフリーレン')

    await userEvent.click(screen.getByRole('button', { name: '确认信息并生成计划' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ title: '葬送のフリーレン' }))
  })

  it('hydrates S02E12 and submits numeric season and episode values', async () => {
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 2,
      payload: {
        title: '季度番剧',
        season: 2,
        episode: 12,
        is_movie: false,
        release_tag: 'WEB-DL',
        evidence: [],
      },
    }} onSubmit={onSubmit} />)

    expect(screen.getByLabelText('标题')).toHaveValue('季度番剧')
    expect(screen.getByLabelText('季度')).toHaveValue(2)
    expect(screen.getByLabelText('集号')).toHaveValue('12')
    expect(screen.getByText(/目标路径预览/).parentElement).toHaveTextContent('S02E12')

    await userEvent.click(screen.getByRole('button', { name: '确认信息并生成计划' }))

    expect(onSubmit).toHaveBeenCalledWith({
      title: '季度番剧',
      media_type: 'episode',
      season: 2,
      episode: 12,
      release_tag: 'WEB-DL',
      manual_lock: true,
    })
  })

  it('preserves season zero and an SP string episode', async () => {
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 3,
      payload: {
        title: '番剧特典',
        season: 0,
        episode: 'SP03',
        is_movie: false,
        release_tag: '',
        evidence: [{ agent: 'filename', value: 'SP03', confidence: 0.9 }],
      },
    }} onSubmit={onSubmit} />)

    expect(screen.getByLabelText('媒体类型')).toHaveValue('special')
    expect(screen.getByLabelText('季度')).toHaveValue(0)
    expect(screen.getByLabelText('集号')).toHaveValue('SP03')
    expect(screen.getByText(/目标路径预览/).parentElement).toHaveTextContent('Specials')

    await userEvent.click(screen.getByRole('button', { name: '确认信息并生成计划' }))

    expect(onSubmit).toHaveBeenCalledWith({
      title: '番剧特典',
      media_type: 'special',
      season: 0,
      episode: 'SP03',
      release_tag: '',
      manual_lock: true,
    })
  })

  it('submits a decimal episode without truncating it', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 4,
      payload: { title: '分段番剧', season: 1, episode: 12, is_movie: false, evidence: [] },
    }} onSubmit={onSubmit} />)

    await user.clear(screen.getByLabelText('集号'))
    await user.type(screen.getByLabelText('集号'), '12.5')
    await user.click(screen.getByRole('button', { name: '确认信息并生成计划' }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ episode: 12.5 }))
  })

  it('clears episode fields for movie and initializes clean episode fields when switching back', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 5,
      payload: { title: '切换番剧', season: 2, episode: 12, is_movie: false, evidence: [] },
    }} onSubmit={onSubmit} />)

    await user.selectOptions(screen.getByLabelText('媒体类型'), 'movie')
    expect(screen.queryByLabelText('季度')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('集号')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('媒体类型'), 'episode')
    expect(screen.getByLabelText('季度')).toHaveValue(1)
    expect(screen.getByLabelText('集号')).toHaveValue('')
    expect(screen.getByRole('button', { name: '确认信息并生成计划' })).toBeDisabled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('initializes special fields and does not leak an SP token back into episode submission', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<ReviewResolutionForm review={{
      id: 6,
      payload: { title: '切换特典', season: 1, episode: 8, is_movie: false, evidence: [] },
    }} onSubmit={onSubmit} />)

    await user.selectOptions(screen.getByLabelText('媒体类型'), 'special')
    expect(screen.getByLabelText('季度')).toHaveValue(0)
    expect(screen.getByLabelText('集号')).toHaveValue('SP01')

    await user.selectOptions(screen.getByLabelText('媒体类型'), 'episode')
    expect(screen.getByLabelText('季度')).toHaveValue(1)
    expect(screen.getByLabelText('集号')).toHaveValue('')
    await user.type(screen.getByLabelText('集号'), '3')
    await user.click(screen.getByRole('button', { name: '确认信息并生成计划' }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      media_type: 'episode',
      season: 1,
      episode: 3,
    }))
  })
})
