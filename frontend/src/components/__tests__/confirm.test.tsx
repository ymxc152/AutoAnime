/*
 * ConfirmHost 单测:Esc 关闭以 false resolve;确认/取消正常落地;
 * 并发请求时前一个未决确认被覆盖、以 false 安全 resolve(不悬挂)。
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

  it('并发:前一个未决确认被第二个覆盖,以 false 安全 resolve', async () => {
    render(<ConfirmHost />)
    const first = ask('第一个确认')
    expect(await screen.findByText('第一个确认')).toBeInTheDocument()
    const second = ask('第二个确认')
    await expect(first).resolves.toBe(false)
    expect(await screen.findByText('第二个确认')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: '确认' }))
    await expect(second).resolves.toBe(true)
  })
})
