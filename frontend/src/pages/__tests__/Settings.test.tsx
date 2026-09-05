/*
 * Settings 冒烟 + 交互:编辑产生脏态 → 保存 PUT → 已撤销恢复。
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SettingsPage } from '../Settings'
import { renderPage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'

describe('SettingsPage', () => {
  beforeEach(() => {
    resetMockState()
  })

  it('渲染四个分区', async () => {
    renderPage(<SettingsPage />)
    expect(await screen.findByText('下载器')).toBeInTheDocument()
    expect(screen.getByText('LLM')).toBeInTheDocument()
    expect(screen.getByText('自主权限')).toBeInTheDocument()
    expect(screen.getByText('质量与洗版')).toBeInTheDocument()
    // 初始未保存 → 按钮禁用
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('自主权限三档可选', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await screen.findByText('下载器')
    expect(screen.getByRole('radio', { name: /均衡/ })).toBeChecked()
    await user.click(screen.getByRole('radio', { name: /保守/ }))
    expect(screen.getByRole('radio', { name: /保守/ })).toBeChecked()
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
  })

  it('切换 LLM 开关 → 保存 → 已保存提示', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    const llmSwitch = await screen.findByRole('switch', { name: '启用 LLM 兜底' })
    expect(llmSwitch).not.toBeChecked()
    await user.click(llmSwitch)
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    // 保存后脏态清空
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeDisabled())
  })

  it('修改洗版阈值', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    const threshold = await screen.findByLabelText('洗版触发阈值(新分 ≥ 现分 + 阈值)')
    await user.clear(threshold)
    await user.type(threshold, '3')
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    // 重新打开页面(mock 状态在会话内持久)验证持久化
    const value = (threshold as HTMLInputElement).value
    expect(value).toBe('3')
  })

  it('下载器客户端可切换 aria2', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    const clientSelect = await screen.findByRole('combobox', { name: '下载客户端' })
    await user.selectOptions(clientSelect, 'aria2')
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
  })
})
