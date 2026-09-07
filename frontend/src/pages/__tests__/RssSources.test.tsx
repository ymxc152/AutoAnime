/*
 * RSSSources 冒烟 + 交互(对齐后端 RssSourceCreateIn:season_id 必填):
 * 表格、启停开关、移除确认、创建校验。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RssSourcesPage } from '../RssSources'
import { renderPage } from '../../test/testUtils'
import { api, ApiError } from '../../api'
import type { RssSourceDto } from '../../api/types'
import { resetMockState } from '../../mocks/handlers'

describe('RssSourcesPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染源表格', async () => {
    renderPage(<RssSourcesPage />)
    expect(
      await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***'),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('switch').length).toBe(3)
  })

  it('启停开关切换后调用 PATCH', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    const switches = await screen.findAllByRole('switch')
    expect(switches[0]).toBeChecked()
    await user.click(switches[0]!)
    await waitFor(async () => {
      const after = await screen.findAllByRole('switch')
      expect(after[0]).not.toBeChecked()
    })
  })

  it('移除需二次确认', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByTitle('https://bangumi.moe/rss/moe/6556')
    const removeButtons = screen.getAllByRole('button', { name: '移除' })
    await user.click(removeButtons[removeButtons.length - 1]!)
    expect(await screen.findByRole('button', { name: '确认' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await waitFor(() =>
      expect(screen.queryByTitle('https://bangumi.moe/rss/moe/6556')).not.toBeInTheDocument(),
    )
  })

  it('回归 A2:启停失败不再静默,展示 role=alert 错误条且开关不翻转,重试可恢复', async () => {
    const user = userEvent.setup()
    const updateSpy = vi
      .spyOn(api.rssSources, 'update')
      .mockRejectedValueOnce(new ApiError(503, 'backend busy'))
    renderPage(<RssSourcesPage />)
    const switches = await screen.findAllByRole('switch')
    expect(switches[0]).toBeChecked()
    await user.click(switches[0]!)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('操作失败')
    expect(alert).toHaveTextContent('backend busy')
    // 失败后不做乐观更新:开关保持原状
    expect(screen.getAllByRole('switch')[0]).toBeChecked()
    // 恢复后重试:翻转成功,错误条消失
    updateSpy.mockRestore()
    await user.click(screen.getAllByRole('switch')[0]!)
    await waitFor(() => expect(screen.getAllByRole('switch')[0]).not.toBeChecked())
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('回归 A2:移除失败不再静默,错误条展示且行保留,重试可恢复', async () => {
    const user = userEvent.setup()
    const removeSpy = vi
      .spyOn(api.rssSources, 'remove')
      .mockRejectedValueOnce(new ApiError(409, 'source in use'))
    renderPage(<RssSourcesPage />)
    await screen.findByTitle('https://bangumi.moe/rss/moe/6556')
    const removeButtons = screen.getAllByRole('button', { name: '移除' })
    await user.click(removeButtons[removeButtons.length - 1]!)
    await user.click(await screen.findByRole('button', { name: '确认' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('source in use')
    expect(screen.getByTitle('https://bangumi.moe/rss/moe/6556')).toBeInTheDocument()
    // 恢复后重试:行移除,错误条消失
    removeSpy.mockRestore()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await waitFor(() =>
      expect(screen.queryByTitle('https://bangumi.moe/rss/moe/6556')).not.toBeInTheDocument(),
    )
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('空地址提交显示校验错误', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***')
    await user.click(screen.getByRole('button', { name: '添加' }))
    expect(await screen.findByText('请填写源地址')).toBeInTheDocument()
  })

  it('缺关联季提交显示校验错误(对齐后端 season_id 必填)', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***')
    await user.type(screen.getByLabelText('地址'), 'https://mikanani.me/RSS/Bangumi?subgroupid=583')
    await user.click(screen.getByRole('button', { name: '添加' }))
    // 错误文案与下拉占位同串:限定 Field 的错误 <p>
    expect(
      await screen.findByText('请选择关联季', { selector: 'p.text-xs' }),
    ).toBeInTheDocument()
  })

  it('填写地址与关联季后创建成功', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***')
    await user.type(screen.getByLabelText('地址'), 'https://mikanani.me/RSS/Bangumi?subgroupid=583')
    await user.selectOptions(screen.getByLabelText('关联季'), '2')
    await user.click(screen.getByRole('button', { name: '添加' }))
    expect(
      await screen.findByTitle('https://mikanani.me/RSS/Bangumi?subgroupid=583'),
    ).toBeInTheDocument()
  })

  it('回归 B2:关联季为下拉框,选项来自订阅季(番名+季号+ID 拼文案)', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    const select = (await screen.findByLabelText('关联季')) as HTMLSelectElement
    expect(select.tagName).toBe('SELECT')
    // 默认占位;等订阅数据落地后出现 3 个季选项(药屋 S2 / 迷宫饭 S1 / 芙莉莲 S1)
    expect(select).toHaveValue('')
    expect(
      await screen.findByRole('option', { name: /药屋少女的呢喃 · 第 2 季\(ID 2\)/ }),
    ).toBeInTheDocument()
    expect(select.options.length).toBe(4)
    expect(screen.getByRole('option', { name: /迷宫饭 · 第 1 季\(ID 6\)/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /葬送的芙莉莲 · 第 1 季\(ID 1\)/ })).toBeInTheDocument()
    // 选择后以 season id 提交
    await user.selectOptions(select, '6')
    expect(select).toHaveValue('6')
  })

  it('回归 B2:表格季列显示番名+季号;解析不到的旧数据回显原 season id', async () => {
    const source: RssSourceDto = {
      id: 99,
      url: 'https://example.com/rss/legacy',
      has_token: false,
      season_id: 999,
      enabled: true,
      last_polled_at: null,
    }
    const listSpy = vi
      .spyOn(api.rssSources, 'list')
      .mockResolvedValue({ total: 1, limit: 100, offset: 0, items: [source] })
    renderPage(<RssSourcesPage />)
    expect(await screen.findByTitle('https://example.com/rss/legacy')).toBeInTheDocument()
    // season_id 999 不在订阅季列表 → 回显原 id(旧数据兼容),不伪装成季名
    expect(screen.getByText('999')).toBeInTheDocument()
    listSpy.mockRestore()
  })

  it('编辑 RSS 源:URL 与 token 可更新,空 token 不修改旧 token', async () => {
    const user = userEvent.setup()
    const actualUpdate = api.rssSources.update
    const updateSpy = vi.spyOn(api.rssSources, 'update').mockImplementationOnce(async (...args) => actualUpdate(...args))
    renderPage(<RssSourcesPage />)
    const row = (await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***')).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: '编辑 RSS 源' }))
    const dialog = screen.getByRole('dialog')
    const urlInput = within(dialog).getByLabelText('地址')
    await user.clear(urlInput)
    await user.type(urlInput, 'https://mikanani.me/RSS/Updated')
    await user.type(within(dialog).getByLabelText('令牌(可选)'), 'new-secret')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(updateSpy).toHaveBeenCalledWith(1, {
      url: 'https://mikanani.me/RSS/Updated',
      enabled: true,
      token: 'new-secret',
    })
    expect(await screen.findByTitle('https://mikanani.me/RSS/Updated')).toBeInTheDocument()
  })

  it('编辑 RSS 源:清除 Token 显式提交 null,状态变未配置', async () => {
    const user = userEvent.setup()
    const actualUpdate = api.rssSources.update
    const updateSpy = vi.spyOn(api.rssSources, 'update').mockImplementationOnce(async (...args) => actualUpdate(...args))
    renderPage(<RssSourcesPage />)
    const row = (await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***')).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: '编辑 RSS 源' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByLabelText('清除 Token'))
    expect(within(dialog).getByLabelText('令牌(可选)')).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(updateSpy).toHaveBeenCalledWith(1, {
      url: 'https://mikanani.me/RSS/MyBangumi?token=***',
      enabled: true,
      token: null,
    })
    await waitFor(() => expect(within(row).getByText('未配置')).toBeInTheDocument())
  })

  it('编辑 RSS 源失败:错误展示在抽屉内且不关闭', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.rssSources, 'update').mockRejectedValueOnce(new ApiError(409, 'source locked'))
    renderPage(<RssSourcesPage />)
    const row = (await screen.findByTitle('https://mikanani.me/RSS/MyBangumi?token=***')).closest('tr') as HTMLElement
    await user.click(within(row).getByRole('button', { name: '编辑 RSS 源' }))
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('source locked')
  })
})
