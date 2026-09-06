/*
 * useFocusTrap —— Tab 循环限制在容器内、active 时 focus 到第一个可聚焦元素、
 * 清理时归还 focus。jsdom 下用按钮(input 天然可聚焦)验证,不依赖 div tabindex。
 * 归还用例遵循真实时序:触发按钮先持有焦点 → 打开 trap → 关闭 → 归还
 * (jsdom 的 click 不触发 focus,须显式 focus 触发元素)。
 */
import { describe, expect, it, afterEach } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { useRef, useState } from 'react'
import { useFocusTrap } from '../useFocusTrap'

afterEach(cleanup)

function TrapHarness({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useFocusTrap(ref, active)
  return (
    <div>
      <button type="button" data-testid="trigger">trigger</button>
      {active && (
        <div ref={ref} data-testid="trap">
          <button type="button" data-testid="first">first</button>
          <input data-testid="middle" />
          <button type="button" data-testid="last">last</button>
        </div>
      )}
    </div>
  )
}

describe('useFocusTrap', () => {
  it('active 时 focus 到容器内第一个可聚焦元素', async () => {
    const { getByTestId } = render(<TrapHarness active />)
    await waitFor(() => {
      expect(document.activeElement).toBe(getByTestId('first'))
    })
  })

  it('Tab 在最后一个元素上循环到第一个', async () => {
    const { getByTestId } = render(<TrapHarness active />)
    await waitFor(() => {
      expect(document.activeElement).toBe(getByTestId('first'))
    })
    getByTestId('last').focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(getByTestId('first'))
  })

  it('Shift+Tab 在第一个元素上循环到最后一个', async () => {
    const { getByTestId } = render(<TrapHarness active />)
    await waitFor(() => {
      expect(document.activeElement).toBe(getByTestId('first'))
    })
    getByTestId('first').focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(getByTestId('last'))
  })

  it('Tab 在中间元素正常前进', async () => {
    const { getByTestId } = render(<TrapHarness active />)
    await waitFor(() => {
      expect(document.activeElement).toBe(getByTestId('first'))
    })
    getByTestId('middle').focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(getByTestId('last'))
  })

  it('inactive 时归还 focus 到触发元素', async () => {
    function Harness() {
      const [active, setActive] = useState(false)
      const ref = useRef<HTMLDivElement>(null)
      useFocusTrap(ref, active)
      return (
        <div>
          <button type="button" data-testid="trigger" onClick={() => setActive(true)}>trigger</button>
          {active && (
            <div ref={ref} data-testid="trap">
              <button type="button" data-testid="first">first</button>
              <button type="button" data-testid="close" onClick={() => setActive(false)}>close</button>
            </div>
          )}
        </div>
      )
    }
    const { getByTestId } = render(<Harness />)
    // 真实时序:触发元素持有焦点 → 打开 → 关闭 → 归还
    getByTestId('trigger').focus()
    fireEvent.click(getByTestId('trigger'))
    await waitFor(() => {
      expect(document.activeElement).toBe(getByTestId('first'))
    })
    fireEvent.click(getByTestId('close'))
    await waitFor(() => {
      expect(document.activeElement).toBe(getByTestId('trigger'))
    })
  })
})
