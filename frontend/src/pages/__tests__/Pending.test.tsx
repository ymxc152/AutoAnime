/*
 * Pending 冒烟 + 核心交互(人工介入主战场):
 * 队列列表 → 抽屉 diff 视图(逐字段证据来源标注)→ 纠正表单提交 → 行消失。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PendingPage } from '../Pending'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

describe('PendingPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染待确认队列', async () => {
    renderPage(<PendingPage />)
    expect(
      await screen.findByText('[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv'),
    ).toBeInTheDocument()
    expect(screen.getByText(/共 4 条/)).toBeInTheDocument()
  })

  it('逐条拒绝后展示空态文案', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)
    // 每轮等该行消失(mock 拉取有延迟),避免点中陈旧行
    const rawNames = [
      '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv',
      'Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]',
      'Sousou no Frieren S1 - 12v2 (B-Global 1920x1080 WebRip AAC).mkv',
      'Sousou no Frieren S1 - 24 (B-Global 1920x1080 WebRip AAC).mkv',
    ]
    for (const name of rawNames) {
      await user.click(screen.getAllByRole('button', { name: '纠正' })[0]!)
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: '拒绝' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      await waitFor(() => expect(screen.queryByText(name)).not.toBeInTheDocument())
    }
    expect(await screen.findByText('队列为空,没有需要人工确认的解析结果。')).toBeInTheDocument()
  })

  it('diff 视图逐字段标注证据来源(name/folder/memory/llm)', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    // 第 2 条:Kusuriya(记忆/目录/文件名混合证据)
    await user.click(
      (await screen.findByText('Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]'))
        .closest('tr')!
        .querySelector('button')!,
    )
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getAllByText('文件名').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('目录').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('记忆').length).toBeGreaterThan(0)
    // 字段值以 mono 呈现
    expect(within(dialog).getByText('药屋少女的呢喃')).toBeInTheDocument()
  })

  it('纠正表单提交后触发学习并从队列移除', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    expect(await screen.findByText(/共 4 条/)).toBeInTheDocument()

    await user.click(
      (await screen.findByText('Sousou no Frieren S1 - 12v2 (B-Global 1920x1080 WebRip AAC).mkv'))
        .closest('tr')!
        .querySelector('button')!,
    )
    const dialog = await screen.findByRole('dialog')
    // 修正集数
    const episodeInput = within(dialog).getByLabelText('集')
    await user.clear(episodeInput)
    await user.type(episodeInput, '13')
    await user.click(within(dialog).getByRole('button', { name: '提交纠正' }))

    // 提交成功:抽屉关闭,队列 4 → 3
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/共 3 条/)).toBeInTheDocument())
    expect(
      screen.queryByText('Sousou no Frieren S1 - 12v2 (B-Global 1920x1080 WebRip AAC).mkv'),
    ).not.toBeInTheDocument()
  })

  it('按当前结果确认', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await user.click(
      (await screen.findByText('[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv'))
        .closest('tr')!
        .querySelector('button')!,
    )
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '按此结果确认' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/共 3 条/)).toBeInTheDocument())
  })
})
