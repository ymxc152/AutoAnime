/*
 * Logs 用户可读视图测试:
 *  - 时间列:组行 last_created_at / 明细行 created_at → 本地时区 'MM-DD HH:mm';
 *    字段缺省(null/undefined,后端 0008 迁移前或未上线)→「—」,不报错
 *  - 中文事件映射:entity/action → 「动作 · 对象」中文,未知值原样回退英文
 *  - operation_id 降级:只显示 8 位前缀,title 悬浮保留完整值,复制按钮保留
 *  - 交互不回归:搜索按原始值过滤、展开明细懒加载、组级/行级撤销
 *
 * fixtures(mocks/data.ts)不带 created_at/last_created_at(他人维护,不可改),
 * 带时间的用例通过 spyOn api 端点注入受控数据;期望展示值用同口径独立推导,
 * 与运行时区解耦。
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LogsPage } from '../Logs'
import { renderPage } from '../../test/testUtils'
import { api } from '../../api'
import type { AuditDto, OperationGroupDto, Page } from '../../api/types'
import { resetMockState } from '../../mocks/handlers'

function asPage<T>(items: T[]): Page<T> {
  return { total: items.length, limit: 100, offset: 0, items }
}

/** 与组件同口径独立推导期望展示值(本地时区),断言不与特定时区耦合 */
function localLabel(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

describe('LogsPage 时间列(用户可读视图)', () => {
  beforeEach(() => {
    resetMockState()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('组行显示本地时区时间 + 中文事件 + 条数,operation_id 降级为 8 位前缀', async () => {
    const operationId = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'
    const group: OperationGroupDto = {
      operation_id: operationId,
      rows: 2,
      entities: ['episode'],
      actions: ['episode.organized'],
      first_audit_id: 11,
      last_audit_id: 12,
      last_created_at: '2026-09-05T14:30:00Z',
      rollbackable: false,
    }
    vi.spyOn(api.auditOperations, 'list').mockResolvedValue(asPage([group]))

    renderPage(<LogsPage />)

    // 时间列:本地时区 MM-DD HH:mm
    expect(await screen.findByText(localLabel('2026-09-05T14:30:00Z'))).toBeInTheDocument()
    // 中文事件「动作 · 对象」+ 条数徽标
    expect(screen.getByText('归档 · 剧集')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    // 哈希降级:8 位前缀可见,完整值不在正文,title 悬浮保留
    expect(screen.getByText('a1b2c3d4')).toBeInTheDocument()
    expect(screen.queryByText(operationId)).not.toBeInTheDocument()
    expect(screen.getByTitle(operationId)).toBeInTheDocument()
  })

  it('组时间缺省(null / 字段未上线 undefined)显示「—」', async () => {
    const groups: OperationGroupDto[] = [
      {
        operation_id: 'nullcase0000000000000000000000aa',
        rows: 1,
        entities: ['settings'],
        actions: ['settings.updated'],
        first_audit_id: 1,
        last_audit_id: 1,
        last_created_at: null,
        rollbackable: false,
      },
      {
        // 旧后端字段未上线:响应里干脆没有 last_created_at 键
        operation_id: 'legacycase0000000000000000000000bb',
        rows: 1,
        entities: ['settings'],
        actions: ['settings.notify_test'],
        first_audit_id: 2,
        last_audit_id: 2,
        rollbackable: false,
      },
    ]
    vi.spyOn(api.auditOperations, 'list').mockResolvedValue(asPage(groups))

    renderPage(<LogsPage />)

    expect(await screen.findByText('更新设置 · 设置')).toBeInTheDocument()
    expect(screen.getByText('通知通道测试 · 设置')).toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(2)
  })

  it('明细行显示行级时间与中文事件;created_at 为 null 的历史行回退「—」', async () => {
    const operationId = 'c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6'
    const at = '2026-09-06T08:05:00+08:00'
    vi.spyOn(api.auditOperations, 'list').mockResolvedValue(
      asPage<OperationGroupDto>([
        {
          operation_id: operationId,
          rows: 2,
          entities: ['parse_memory', 'episode'],
          actions: ['memory_hit', 'rollback'],
          first_audit_id: 21,
          last_audit_id: 22,
          last_created_at: at,
          rollbackable: false,
        },
      ]),
    )
    const entries: AuditDto[] = [
      {
        id: 22,
        operation_id: operationId,
        entity: 'parse_memory',
        entity_id: 9,
        action: 'memory_hit',
        created_at: at,
        instruction: { raw_name: 'Kusuriya no Hitorigoto - 17' },
        reverse: {},
        actor: 'auto',
      },
      {
        // 0008 迁移前历史行:created_at = null
        id: 21,
        operation_id: operationId,
        entity: 'episode',
        entity_id: null,
        action: 'rollback',
        created_at: null,
        instruction: { rolled_back_audit_id: 20 },
        reverse: { rollback_of: 20 },
        actor: 'manual',
      },
    ]
    vi.spyOn(api.audit, 'list').mockResolvedValue(asPage(entries))

    const user = userEvent.setup()
    renderPage(<LogsPage />)
    // 组摘要 = 组内动作/对象并集(两个动作、两个对象)
    await user.click(await screen.findByText('命中记忆、撤销整理 · 识别记忆、剧集'))

    // 明细行中文事件 + 行级时间(组行与明细行同一时刻 → 2 处)
    expect(await screen.findByText('撤销整理 · 剧集')).toBeInTheDocument()
    expect(screen.getAllByText(localLabel(at))).toHaveLength(2)
    // 历史行时间缺省 →「—」
    expect(screen.getAllByText('—')).toHaveLength(1)
    // 对象 id 徽标(title 用 strings.logs.entity)
    expect(screen.getByTitle('对象 #9')).toBeInTheDocument()
  })

  it('未知 entity/action 原样回退英文,不译不崩', async () => {
    vi.spyOn(api.auditOperations, 'list').mockResolvedValue(
      asPage<OperationGroupDto>([
        {
          operation_id: 'deadbeefdeadbeefdeadbeefdeadbeef',
          rows: 1,
          entities: ['future_entity'],
          actions: ['future.action'],
          first_audit_id: 1,
          last_audit_id: 1,
          last_created_at: null,
          rollbackable: false,
        },
      ]),
    )
    renderPage(<LogsPage />)
    expect(await screen.findByText('future.action · future_entity')).toBeInTheDocument()
  })
})

describe('LogsPage(分组契约冒烟,mock fixtures 无时间字段)', () => {
  beforeEach(() => {
    resetMockState()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('按后端分组端点渲染操作组:中文事件 + 时间「—」+ 8 位前缀,完整哈希不上正文', async () => {
    renderPage(<LogsPage />)
    expect(await screen.findByText('降级待确认 · 识别记忆')).toBeInTheDocument()
    expect(screen.getByText('命中记忆 · 识别记忆')).toBeInTheDocument()
    expect(screen.getByText('确认识别 · 待确认')).toBeInTheDocument()
    // 时间缺省(mock fixtures 未带 last_created_at)→「—」×3
    expect(screen.getAllByText('—')).toHaveLength(3)
    // 三个组 id 前 8 位同为 'op-20260':前缀降级后各显示一次
    expect(screen.getAllByText('op-20260')).toHaveLength(3)
    expect(screen.queryByText('op-20260905-0003')).not.toBeInTheDocument()
    expect(screen.getByTitle('op-20260905-0003')).toBeInTheDocument()
    // 复制按钮保留
    expect(screen.getAllByRole('button', { name: '复制操作 ID' })).toHaveLength(3)
  })

  it('展开分组懒加载明细行(instruction/reverse JSON + 中文事件)', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await user.click(await screen.findByText('命中记忆 · 识别记忆'))
    // 该组明细:memory_hit 行,组摘要 + 明细行 ≥2 处
    await waitFor(() => {
      expect(screen.getAllByText('命中记忆 · 识别记忆').length).toBeGreaterThanOrEqual(2)
    })
    expect(screen.getAllByText('instruction').length).toBeGreaterThan(0)
    expect(screen.getByText(/Kusuriya no Hitorigoto/)).toBeInTheDocument()
    // actor 徽标
    expect(screen.getByText('自动')).toBeInTheDocument()
  })

  it('搜索框 placeholder 不变,仍按原始 operation_id/entity/action 过滤', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    expect(screen.getByPlaceholderText('搜索操作 ID / 对象…')).toBeInTheDocument()
    await screen.findByText('降级待确认 · 识别记忆')
    // 原始英文 action 值(后端存储口径)可搜
    await user.type(screen.getByRole('searchbox'), 'pending_confirm')
    expect(screen.queryByText('降级待确认 · 识别记忆')).not.toBeInTheDocument()
    expect(screen.getByText('确认识别 · 待确认')).toBeInTheDocument()
  })

  it('搜索按操作 ID 过滤', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    await screen.findByText('降级待确认 · 识别记忆')
    await user.type(screen.getByRole('searchbox'), 'op-20260905-0002')
    expect(screen.queryByText('降级待确认 · 识别记忆')).not.toBeInTheDocument()
    expect(screen.getByText('命中记忆 · 识别记忆')).toBeInTheDocument()
  })

  it('撤销整理:二次确认后以组内最新 audit 行 id 执行,成功显示已撤销', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    const row = (await screen.findByText('降级待确认 · 识别记忆')).closest('li')!
    await user.click(within(row).getByRole('button', { name: '撤销整理' }))
    // 二次确认文案带条数,确认后执行
    expect(within(row).getByText('撤销这 1 条操作？')).toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: '确认' }))
    expect(await screen.findByText('已撤销')).toBeInTheDocument()
    // 撤销落新审计组(mock 对齐后端):哈希降级,完整值走 title
    expect(await screen.findByTitle('op-mock-0001')).toBeInTheDocument()
  })

  it('撤销整理:首次点击仅出现确认,取消后不执行', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    const row = (await screen.findByText('降级待确认 · 识别记忆')).closest('li')!
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
    const row = (await screen.findByText('命中记忆 · 识别记忆')).closest('li')!
    expect(within(row).queryByRole('button', { name: '撤销整理' })).not.toBeInTheDocument()
  })

  it('行级撤销:带 reverse 的明细行「撤销此条」→ confirmDialog 确认 → 按行 id 调 rollback(12-F)', async () => {
    // ConfirmHost 未挂载时 confirmDialog 退回 window.confirm,这里确认放行
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const rollbackSpy = vi.spyOn(api.organize, 'rollback')
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    // 展开带 reverse 明细行的组(op-0003 内是 #3 demote_pending)
    await user.click(await screen.findByText('降级待确认 · 识别记忆'))
    // 组摘要与明细行同文案,取展开区里的明细行(第 2 处)
    await waitFor(() =>
      expect(screen.getAllByText('降级待确认 · 识别记忆').length).toBeGreaterThanOrEqual(2),
    )
    const entryItem = screen.getAllByText('降级待确认 · 识别记忆')[1]!.closest('li')!
    await user.click(within(entryItem).getByRole('button', { name: '撤销此条' }))
    // 行级撤销按该明细行的 audit 行 id(3)执行,而非组 last_audit_id
    await waitFor(() => expect(rollbackSpy).toHaveBeenCalledWith(3))
    // 撤销落新审计组,组列表同步刷新
    expect(await screen.findByTitle('op-mock-0001')).toBeInTheDocument()
  })

  it('行级撤销:无 reverse 的明细行不显示「撤销此条」入口(12-F)', async () => {
    const user = userEvent.setup()
    renderPage(<LogsPage />)
    // 展开全无 reverse 的组(op-0002 memory_hit)
    await user.click(await screen.findByText('命中记忆 · 识别记忆'))
    await waitFor(() =>
      expect(screen.getAllByText('命中记忆 · 识别记忆').length).toBeGreaterThanOrEqual(2),
    )
    const entryItem = screen.getAllByText('命中记忆 · 识别记忆')[1]!.closest('li')!
    expect(within(entryItem).queryByRole('button', { name: '撤销此条' })).not.toBeInTheDocument()
  })
})
