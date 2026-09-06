/*
 * Settings 冒烟 + 交互(对齐后端 SettingsOut/SettingsUpdateIn):
 * 白名单运行时覆写(dry_run/l2/llm/reference)→ PUT;只读环境信息;
 * quality/naming 段后端暂缺(E4),只给说明不造假数据。
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SettingsPage } from '../Settings'
import { renderPage } from '../../test/testUtils'
import { api } from '../../api'
import { resetMockState } from '../../mocks/handlers'

describe('SettingsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染运行时开关/只读环境信息/E4 说明三个分区', async () => {
    renderPage(<SettingsPage />)
    expect(await screen.findByText('运行时开关')).toBeInTheDocument()
    expect(screen.getByText('环境信息(只读)')).toBeInTheDocument()
    expect(screen.getByText('质量与洗版')).toBeInTheDocument()
    // E4 说明存在,且不渲染任何 quality/naming 输入
    expect(screen.getByText(/将在 E4 落地后开放配置/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/洗版触发阈值/)).not.toBeInTheDocument()
    // 初始未保存 → 按钮禁用
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('切换 LLM 开关 → 保存 → 已保存提示且脏态清空', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    const llmSwitch = await screen.findByRole('switch', { name: '启用 LLM 兜底' })
    expect(llmSwitch).not.toBeChecked()
    await user.click(llmSwitch)
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeDisabled())
    // 保存后以服务端返回为基线:开关保持开启
    expect(screen.getByRole('switch', { name: '启用 LLM 兜底' })).toBeChecked()
  })

  it('试运行模式开关可用', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    const dryRun = await screen.findByRole('switch', { name: '试运行模式' })
    expect(dryRun).not.toBeChecked()
    await user.click(dryRun)
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
  })

  it('参考源顺序以逗号分隔编辑', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    const order = await screen.findByLabelText('参考源顺序')
    expect(order).toHaveValue('bangumi,tmdb')
    await user.clear(order)
    await user.type(order, 'tmdb,bangumi')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    // 保存即提交:未 blur 的草稿也归一化写入请求
    expect(updateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ reference_order: ['tmdb', 'bangumi'] }),
    )
    updateSpy.mockRestore()
  })

  it('回归 A1:末尾敲逗号不再被回显抹掉,blur 后归一化写回', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    const order = await screen.findByLabelText('参考源顺序')
    await user.clear(order)
    // 逐字输入含末尾逗号的串:草稿保真原文,不再立即 split/回写渲染
    await user.type(order, 'tmdb,bangumi,')
    expect(order).toHaveValue('tmdb,bangumi,')
    // blur 提交:trim/去空后写回 edit,回显归一化
    await user.tab()
    expect(order).toHaveValue('tmdb,bangumi')
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    // 保存后以服务端返回为基线,归一化列表保持展示
    expect(screen.getByLabelText('参考源顺序')).toHaveValue('tmdb,bangumi')
    expect(updateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ reference_order: ['tmdb', 'bangumi'] }),
    )
    updateSpy.mockRestore()
  })

  it('只读环境信息与密钥状态徽标', async () => {
    renderPage(<SettingsPage />)
    expect(await screen.findByText('/library')).toBeInTheDocument()
    expect(screen.getByText('127.0.0.1:8000')).toBeInTheDocument()
    // 密钥只回 has_*:LLM key 已配置,API token 未配置
    expect(screen.getByText('已配置')).toBeInTheDocument()
    expect(screen.getByText('未配置')).toBeInTheDocument()
  })

  it('回归 A3:API Token 本端注入——保存写入 localStorage 并提示刷新,清除移除', async () => {
    const user = userEvent.setup()
    localStorage.removeItem('autoanime-api-token')
    renderPage(<SettingsPage />)
    const tokenInput = await screen.findByLabelText('API Token(本端注入)')
    expect(tokenInput).toHaveAttribute('type', 'password')
    expect(tokenInput).toHaveValue('')
    await user.type(tokenInput, 'sk-test-123')
    await user.click(screen.getByRole('button', { name: '保存 Token' }))
    expect(await screen.findByText('已保存,刷新页面后生效')).toBeInTheDocument()
    // 与 client.ts/sse.ts 同 key:请求头与 SSE query 都从这里读
    expect(localStorage.getItem('autoanime-api-token')).toBe('sk-test-123')
    // 清除:localStorage 移除,提示刷新生效
    await user.click(screen.getByRole('button', { name: '清除 Token' }))
    expect(await screen.findByText('已清除,刷新页面后生效')).toBeInTheDocument()
    expect(localStorage.getItem('autoanime-api-token')).toBeNull()
    // 收尾不污染同文件其他用例
    localStorage.removeItem('autoanime-api-token')
  })
})
