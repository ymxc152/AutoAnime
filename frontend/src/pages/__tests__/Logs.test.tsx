/*
 * Logs 冒烟 + 核心交互(对齐后端分组契约):
 * 组列表来自 /api/audit/operations;展开懒加载 /api/audit?operation_id=;
 * 撤销以组内最新 audit 行 id(last_audit_id)执行,404/409 语义如实展示。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LogsPage } from '../Logs'
import { renderPage } from '../../test/testUtils'
import { api } from '../../api'
import { resetMockState } from '../../mocks/handlers'

describe('LogsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('按后端分组端点渲染操作组(最新组在前)', async () => {
    renderPage(<LogsPage />)
    expect(await screen.findByText('op-20260905-0003')).toBeInTheDocument()
    expect(screen.getByText('op-20260905-0002')).toBeInTheDocument()
    expect(screen.getByText('op-20260905-0001')).toBeInTheDocument()
    // 组上的动作徽标
    expect(screen.getByText('demote_pending')).toBeInTheDocument()
    expect(screen.getByText('memory_hit')).toBeInTheDocument()
  })

  it('展开分组懒加载明细行(instruction/reverse JSON)', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await user.click(await screen.findByText('op-20260905-0002'))
    // 该组明细:memory_hit 行,含 raw_name instruction(组徽标 + 明细行 ≥2 处)
    await waitFor(() => {
      expect(screen.getAllByText('memory_hit').length).toBeGreaterThanOrEqual(2)
    })
    expect(screen.getAllByText('instruction').length).toBeGreaterThan(0)
    expect(screen.getByText(/Kusuriya no Hitorigoto/)).toBeInTheDocument()
    // actor 徽标
    expect(screen.getByText('自动')).toBeInTheDocument()
  })

  it('撤销整理:二次确认后以组内最新 audit 行 id 执行,成功显示已撤销', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    const row = (await screen.findByText('op-20260905-0003')).closest('li')!
    await user.click(within(row).getByRole('button', { name: '撤销整理' }))
    // 二次确认文案带条数,确认后执行
    expect(within(row).getByText('撤销这 1 条操作？')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: '确认' }))
    expect(await screen.findByText('已撤销')).toBeInTheDocument()
    // 撤销落新审计组(mock 对齐后端行为)
    expect(await screen.findByText('op-mock-0001')).toBeInTheDocument()
  })

  it('撤销整理:首次点击仅出现确认,取消后不执行', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    const row = (await screen.findByText('op-20260905-0003')).closest('li')!
    await user.click(within(row).getByRole('button', { name: '撤销整理' }))
    expect(within(row).getByText('撤销这 1 条操作？')).toBeInTheDocument()
    // 未点确认:无已撤销提示、无新审计组
    expect(screen.queryByText('已撤销')).not.toBeInTheDocument()
    // 取消回到初始按钮态
    await user.click(within(row).getByRole('button', { name: '取消' }))
    expect(within(row).getByRole('button', { name: '撤销整理' })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '确认' })).not.toBeInTheDocument()
  })

  it('非可回滚组隐藏撤销入口,避免无意义 409', async () => {
    renderPage(<LogsPage />)
    const row = (await screen.findByText('op-20260905-0002')).closest('li')!
    expect(within(row).queryByRole('button', { name: '撤销整理' })).not.toBeInTheDocument()
  })

  it('搜索过滤操作 ID', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await screen.findByText('op-20260905-0003')
    await user.type(screen.getByRole('searchbox'), '0002')
    expect(screen.queryByText('op-20260905-0003')).not.toBeInTheDocument()
    expect(screen.getByText('op-20260905-0002')).toBeInTheDocument()
  })

  it('搜索可按动作过滤', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await screen.findByText('op-20260905-0003')
    await user.type(screen.getByRole('searchbox'), 'pending_confirm')
    expect(screen.queryByText('op-20260905-0003')).not.toBeInTheDocument()
    expect(screen.getByText('op-20260905-0001')).toBeInTheDocument()
  })

  it('行级撤销:带 reverse 的明细行「撤销此条」→ confirmDialog 确认 → 按行 id 调 rollback(12-F)', async () => {
    // ConfirmHost 未挂载时 confirmDialog 退回 window.confirm,这里确认放行
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const rollbackSpy = vi.spyOn(api.organize, 'rollback')
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    // 展开带 reverse 明细行的组(op-0003 内是 #3 demote_pending)
    await user.click(await screen.findByText('op-20260905-0003'))
    // 组徽标与明细行同名,取展开区里的明细行(第 2 处)
    await waitFor(() => expect(screen.getAllByText('demote_pending').length).toBeGreaterThanOrEqual(2))
    const entryItem = screen.getAllByText('demote_pending')[1]!.closest('li')!
    await user.click(within(entryItem).getByRole('button', { name: '撤销此条' }))
    // 行级撤销按该明细行的 audit 行 id(3)执行,而非组 last_audit_id
    await waitFor(() => expect(rollbackSpy).toHaveBeenCalledWith(3))
    // 撤销落新审计组,组列表同步刷新
    expect(await screen.findByText('op-mock-0001')).toBeInTheDocument()
  })

  it('行级撤销:无 reverse 的明细行不显示「撤销此条」入口(12-F)', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    // 展开全无 reverse 的组(op-0002 memory_hit)
    await user.click(await screen.findByText('op-20260905-0002'))
    await waitFor(() => expect(screen.getAllByText('memory_hit').length).toBeGreaterThanOrEqual(2))
    const entryItem = screen.getAllByText('memory_hit')[1]!.closest('li')!
    expect(within(entryItem).queryByRole('button', { name: '撤销此条' })).not.toBeInTheDocument()
  })
})
