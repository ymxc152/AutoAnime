/*
 * Pending 跨页选择单测(12-UX):翻页不再清空 selectedIds,
 * 全选本页只追加当前页,批量操作按 id 集合提交;404(id 已不在库)
 * 的条目从选中集剔除并 toast 提示,其余失败保留可重试。
 *
 * mock fixtures 只有 4 条 pending(单页),跨页场景通过 spy 覆写
 * api.pending.list 注入 40 条合成数据(PAGE_SIZE=20 → 恰好两页)驱动真实分页交互。
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import type { MockInstance } from 'vitest'
import { PendingPage } from '../Pending'
import { renderPage } from '../../test/testUtils'
import { api, ApiError } from '../../api'
import type { Page, PendingItemDto, PendingResolveOut } from '../../api/types'

const TOTAL = 40

function makeItem(id: number): PendingItemDto {
  return {
    id,
    raw_name: `synthetic-item-${id}.mkv`,
    context: { title: '合成条目', season: 1, episode: id, segment: 'episode', fansub: 'Mock' },
    stage: 'parse',
    reason: null,
    status: 'pending',
    resolution: null,
    resolved_by: null,
    created_at: '2026-01-01T00:00:00Z',
    resolved_at: null,
  }
}

const allItems: PendingItemDto[] = Array.from({ length: TOTAL }, (_, i) => makeItem(100 + i))

function resolveOut(id: number): PendingResolveOut {
  return { id, status: 'resolved', resolution: {}, resolved_by: 'manual', learned_entries: 0, bypassed: false }
}

describe('PendingPage 跨页选择(12-UX)', () => {
  let confirmSpy: MockInstance

  beforeEach(() => {
    vi.spyOn(api.pending, 'list').mockImplementation(async (query) => {
      const limit = query?.limit ?? 20
      const offset = query?.offset ?? 0
      const page: Page<PendingItemDto> = {
        total: TOTAL,
        limit,
        offset,
        items: allItems.slice(offset, offset + limit),
      }
      return page
    })
    confirmSpy = vi.spyOn(api.pending, 'confirm').mockResolvedValue(resolveOut(0))
    vi.spyOn(toast, 'warning').mockImplementation(() => 0)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('翻页保留勾选:第 1 页勾 1 条 → 翻页后批量条仍在并提示跨页保留', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    expect(await screen.findByText('synthetic-item-100.mkv')).toBeInTheDocument()

    await user.click(screen.getAllByRole('checkbox')[1]!)
    expect(screen.getByText('已选 1 条')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('synthetic-item-120.mkv')).toBeInTheDocument()

    // 选择未被翻页清空,且提示保留了跨页选择
    expect(screen.getByText('已选 1 条')).toBeInTheDocument()
    expect(screen.getByText('已保留跨页选择的 1 条')).toBeInTheDocument()

    // 全选本页只追加当前页:1(跨页)+ 20(本页)= 21
    await user.click(screen.getByRole('checkbox', { name: '全选本页' }))
    expect(screen.getByText('已选 21 条')).toBeInTheDocument()
  })

  it('批量确认按跨页 id 集合提交,全部成功后清空选择', async () => {
    const user = userEvent.setup()
    renderPage(<PendingPage />)
    await screen.findByText('synthetic-item-100.mkv')
    await user.click(screen.getAllByRole('checkbox')[1]!)
    await user.click(screen.getByRole('button', { name: '下一页' }))
    await screen.findByText('synthetic-item-120.mkv')
    await user.click(screen.getByRole('checkbox', { name: '全选本页' }))
    expect(screen.getByText('已选 21 条')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '批量确认' }))
    await user.click(screen.getByRole('button', { name: '确认 21 条？' }))

    await waitFor(() => expect(api.pending.confirm).toHaveBeenCalledTimes(21))
    // 跨页 id 都被提交:第 1 页选中项 + 第 2 页全选
    expect(api.pending.confirm).toHaveBeenCalledWith(100)
    expect(api.pending.confirm).toHaveBeenCalledWith(120)
    await waitFor(() => expect(screen.queryByText('已选 21 条')).not.toBeInTheDocument())
  })

  it('404 的 id 已不在库:从选中集剔除并 toast 提示,不影响其余提交', async () => {
    const user = userEvent.setup()
    confirmSpy.mockImplementation(async (id: number) => {
      if (id === 101) {
        // 第一条在批量执行前已被其它客户端删除:后端 404
        throw new ApiError(404, 'pending 101 not found')
      }
      return resolveOut(id)
    })
    renderPage(<PendingPage />)
    await screen.findByText('synthetic-item-100.mkv')

    const checkboxes = screen.getAllByRole('checkbox')
    await user.click(checkboxes[1]!)
    await user.click(checkboxes[2]!)
    expect(screen.getByText('已选 2 条')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '批量确认' }))
    await user.click(screen.getByRole('button', { name: '确认 2 条？' }))

    expect(toast.warning).toHaveBeenCalledTimes(1)
    // 404 项剔除、成功项出队 → 选择清空,批量条消失
    await waitFor(() => expect(screen.queryByText('已选 2 条')).not.toBeInTheDocument())
    // 两条都已提交(404 不阻断其余)
    expect(api.pending.confirm).toHaveBeenCalledTimes(2)
  })
})
