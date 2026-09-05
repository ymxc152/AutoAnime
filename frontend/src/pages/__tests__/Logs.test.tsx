/*
 * Logs 冒烟 + 核心交互:operation_id 分组展开、指令 JSON、撤销整理。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LogsPage } from '../Logs'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

describe('LogsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('按 operation_id 分组渲染', async () => {
    renderPage(<LogsPage />)
    expect(await screen.findByText('op-20260905-0003')).toBeInTheDocument()
    expect(screen.getByText('op-20260905-0002')).toBeInTheDocument()
    expect(screen.getByText('op-20260905-0001')).toBeInTheDocument()
  })

  it('点击分组展开记录与 instruction/reverse JSON', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await user.click(await screen.findByText('op-20260905-0002'))
    // instruction 与 reverse 都含 S01E20(正向/逆向路径),各有 JSON 块
    const pathMatches = await screen.findAllByText(/S01E20/)
    expect(pathMatches.length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('instruction').length).toBeGreaterThan(0)
    expect(screen.getAllByText('reverse').length).toBeGreaterThan(0)
    // actor 徽标
    expect(screen.getByText('自动')).toBeInTheDocument()
  })

  it('撤销整理:仅 organize 且有 reverse 的分组可用,成功显示已撤销', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    const row = (await screen.findByText('op-20260905-0002')).closest('li')!
    const rollbackButton = within(row).getByRole('button', { name: '撤销整理' })
    expect(rollbackButton).toBeEnabled()
    await user.click(rollbackButton)
    expect(await screen.findByText('已撤销')).toBeInTheDocument()
  })

  it('系统类分组(无 reverse)撤销按钮禁用', async () => {
    renderPage(<LogsPage />)
    const row = (await screen.findByText('op-20260905-0003')).closest('li')!
    // op-0003 的 action 是 organize.rollback 但 reverse 为空 → 不可再撤销
    await waitFor(() => {
      expect(within(row).getByRole('button', { name: '撤销整理' })).toBeDisabled()
    })
  })

  it('搜索过滤 operation_id', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await screen.findByText('op-20260905-0003')
    await user.type(screen.getByRole('searchbox'), '0002')
    expect(screen.queryByText('op-20260905-0003')).not.toBeInTheDocument()
    expect(screen.getByText('op-20260905-0002')).toBeInTheDocument()
  })
})
