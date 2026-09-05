/*
 * RSSSources 冒烟 + 交互(对齐后端 RssSourceCreateIn:season_id 必填):
 * 表格、启停开关、移除确认、创建校验。
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RssSourcesPage } from '../RssSources'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

describe('RssSourcesPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染源表格', async () => {
    renderPage(<RssSourcesPage />)
    expect(
      await screen.findByText('https://mikanani.me/RSS/MyBangumi?token=***'),
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
    await screen.findByText('https://bangumi.moe/rss/moe/6556')
    const removeButtons = screen.getAllByRole('button', { name: '移除' })
    await user.click(removeButtons[removeButtons.length - 1]!)
    expect(await screen.findByRole('button', { name: '确认' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await waitFor(() =>
      expect(screen.queryByText('https://bangumi.moe/rss/moe/6556')).not.toBeInTheDocument(),
    )
  })

  it('空地址提交显示校验错误', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByText('https://mikanani.me/RSS/MyBangumi?token=***')
    await user.click(screen.getByRole('button', { name: '添加' }))
    expect(await screen.findByText('请填写源地址')).toBeInTheDocument()
  })

  it('缺关联季 ID 提交显示校验错误(对齐后端 season_id 必填)', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByText('https://mikanani.me/RSS/MyBangumi?token=***')
    await user.type(screen.getByLabelText('地址'), 'https://mikanani.me/RSS/Bangumi?subgroupid=583')
    await user.click(screen.getByRole('button', { name: '添加' }))
    expect(await screen.findByText('请填写关联季 ID')).toBeInTheDocument()
  })

  it('填写地址与关联季后创建成功', async () => {
    const user = userEvent.setup()
    renderPage(<RssSourcesPage />)
    await screen.findByText('https://mikanani.me/RSS/MyBangumi?token=***')
    await user.type(screen.getByLabelText('地址'), 'https://mikanani.me/RSS/Bangumi?subgroupid=583')
    await user.type(screen.getByLabelText('关联季'), '2')
    await user.click(screen.getByRole('button', { name: '添加' }))
    expect(
      await screen.findByText('https://mikanani.me/RSS/Bangumi?subgroupid=583'),
    ).toBeInTheDocument()
  })
})
