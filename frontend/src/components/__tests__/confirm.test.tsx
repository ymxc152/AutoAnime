/*
 * ConfirmHost 单测:Esc 关闭以 false resolve;确认/取消正常落地;
 * 并发请求排队 —— 同一时刻只显示一个,前一个 settle 后第二个才出现,
 * 各自以用户选择 resolve(12-UX:不再静默覆盖未决请求)。
 */
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmHost } from '../confirm'
import { confirmDialog } from '../../lib/confirm'

/** confirmDialog 会同步 setMessage,包进 act 消除状态更新告警;注意返回的 Promise 不要 await(等确认动作) */
function ask(message: string): Promise<boolean> {
  let pending!: Promise<boolean>
  act(() => {
    pending = confirmDialog(message)
  })
  return pending
}

describe('ConfirmHost', () => {
  it('Esc 关闭 → Promise 以 false resolve', async () => {
    const user = userEvent.setup()
    render(<ConfirmHost />)
    const pending = ask('确定离开吗?')
    expect(await screen.findByText('确定离开吗?')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await expect(pending).resolves.toBe(false)
  })

  it('确认按钮 → true;取消按钮 → false;宿主可复用', async () => {
    const user = userEvent.setup()
    render(<ConfirmHost />)
    const first = ask('第一个确认')
    expect(await screen.findByText('第一个确认')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await expect(first).resolves.toBe(true)
    const second = ask('第二个确认')
    expect(await screen.findByText('第二个确认')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    await expect(second).resolves.toBe(false)
  })

  it('并发:请求排队,前一个 settle 后第二个才出现且各自 resolve(12-UX)', async () => {
    const user = userEvent.setup()
    render(<ConfirmHost />)
    const first = ask('第一个确认')
    expect(await screen.findByText('第一个确认')).toBeInTheDocument()
    const second = ask('第二个确认')
    // 第二个入队等待:仍只显示第一个
    expect(screen.queryByText('第二个确认')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认' }))
    await expect(first).resolves.toBe(true)
    // 第一个 settle 后第二个才弹出,并以自己的选择落地
    expect(await screen.findByText('第二个确认')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '取消' }))
    await expect(second).resolves.toBe(false)
    expect(screen.queryByText('第二个确认')).not.toBeInTheDocument()
  })
})
