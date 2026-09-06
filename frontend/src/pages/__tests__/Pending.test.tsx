/*
 * Pending 冒烟 + 核心交互(人工介入主战场):
 * 队列列表 → 抽屉(context 草稿字段视图)→ 纠正提交(始终带 title)→ 行消失。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PendingPage } from '../Pending'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

/** 打开某行纠正抽屉(行内首按钮现在是 checkbox,需按名找「纠正」) */
async function openCorrectRow(user: ReturnType<typeof userEvent.setup>, rawName: string) {
  const row = (await screen.findByText(rawName)).closest('tr')!
  await user.click(within(row).getByRole('button', { name: '纠正' }))
  return screen.findByRole('dialog')
}


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

  it('抽屉展示 context 草稿字段(后端无证据来源标注,不做来源徽标)', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    const dialog = await openCorrectRow(
      user,
      'Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]',
    )
    // context 草稿:title/season/episode/segment/fansub
    expect(within(dialog).getAllByText('药屋少女的呢喃').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('单集').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('Kamigakari').length).toBeGreaterThan(0)
  })

  it('纠正表单提交:未纠正也始终携带 title(mock 对齐后端 title 必填)', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    expect(await screen.findByText(/共 4 条/)).toBeInTheDocument()

    const dialog = await openCorrectRow(
      user,
      'Sousou no Frieren S1 - 12v2 (B-Global 1920x1080 WebRip AAC).mkv',
    )
    // 只改集数,标题保留原值 → 提交成功(mock 若缺 title 会回 422)
    const episodeInput = within(dialog).getByLabelText('集')
    await user.clear(episodeInput)
    await user.type(episodeInput, '13')
    await user.click(within(dialog).getByRole('button', { name: '提交纠正' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/共 3 条/)).toBeInTheDocument())
  })

  it('清空标题提交时,后端 422 语义如实展示', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)

    const dialog = await openCorrectRow(
      user,
      '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv',
    )
    await user.clear(within(dialog).getByLabelText('标题'))
    await user.click(within(dialog).getByRole('button', { name: '提交纠正' }))
    expect(await screen.findByText(/non-empty 'title'/)).toBeInTheDocument()
    // 抽屉保持打开,队列不变
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/共 4 条/)).toBeInTheDocument()
  })

  it('按当前结果确认', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    const dialog = await openCorrectRow(
      user,
      '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv',
    )
    await user.click(within(dialog).getByRole('button', { name: '按此结果确认' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByText(/共 3 条/)).toBeInTheDocument())
  })

  it('行内快捷确认:单条无二次确认,行消失且计数更新', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)
    const row = screen
      .getByText('[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv')
      .closest('tr')!
    await user.click(within(row).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(screen.getByText(/共 3 条/)).toBeInTheDocument())
    expect(
      screen.queryByText('[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv'),
    ).not.toBeInTheDocument()
  })

  it('行内快捷拒绝:单条无二次确认', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)
    const row = screen
      .getByText('Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]')
      .closest('tr')!
    await user.click(within(row).getByRole('button', { name: '拒绝' }))
    await waitFor(() => expect(screen.getByText(/共 3 条/)).toBeInTheDocument())
    expect(
      screen.queryByText('Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]'),
    ).not.toBeInTheDocument()
  })

  it('批量确认:勾选 2 条 → 轻确认「确认 2 条？」→ 二次点击执行', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)

    const checkboxes = screen.getAllByRole('checkbox')
    // 第 1 个是表头全选,后 4 个是行选择
    await user.click(checkboxes[1]!)
    await user.click(checkboxes[2]!)
    expect(screen.getByText('已选 2 条')).toBeInTheDocument()

    // 第一次点击:只出现轻确认文案,不执行
    await user.click(screen.getByRole('button', { name: '批量确认' }))
    expect(screen.getByRole('button', { name: '确认 2 条？' })).toBeInTheDocument()
    expect(screen.getByText(/共 4 条/)).toBeInTheDocument()

    // 第二次点击:执行,两条均出队
    await user.click(screen.getByRole('button', { name: '确认 2 条？' }))
    await waitFor(() => expect(screen.getByText(/共 2 条/)).toBeInTheDocument())
    expect(screen.queryByText('已选 2 条')).not.toBeInTheDocument()
  })

  it('批量拒绝:轻确认「拒绝 2 条？」后执行', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)

    const checkboxes = screen.getAllByRole('checkbox')
    await user.click(checkboxes[3]!)
    await user.click(checkboxes[4]!)
    await user.click(screen.getByRole('button', { name: '批量拒绝' }))
    expect(screen.getByRole('button', { name: '拒绝 2 条？' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '拒绝 2 条？' }))
    await waitFor(() => expect(screen.getByText(/共 2 条/)).toBeInTheDocument())
  })

  it('分页:Pagination 显示总数,单页时上下页均禁用', async () => {
    renderPage(<PendingPage />)
    await screen.findByText(/共 4 条/)
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  })
})
