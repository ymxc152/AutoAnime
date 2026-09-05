import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})

// ---- jsdom 缺失 API 补丁(@xyflow/react 需要) ----

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

// xyflow d3-zoom 在 fitView 时解析 transform 需要 DOMMatrixReadOnly
if (typeof globalThis.DOMMatrixReadOnly === 'undefined') {
  class DOMMatrixReadOnlyStub {
    m22 = 1
    m41 = 0
    m42 = 0
    constructor(transform?: string) {
      const match = /matrix\(([^)]+)\)/.exec(transform ?? '')
      if (match !== null) {
        const parts = match[1]!.split(',').map(Number)
        this.m22 = parts[3] ?? 1
        this.m41 = parts[4] ?? 0
        this.m42 = parts[5] ?? 0
      }
    }
  }
  ;(globalThis as Record<string, unknown>).DOMMatrixReadOnly = DOMMatrixReadOnlyStub
}

// 无实际布局:让 rAF 立即回调(xyflow 内部测量依赖)
if (typeof globalThis.requestAnimationFrame === 'undefined') {
  globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0) as unknown as number
}
