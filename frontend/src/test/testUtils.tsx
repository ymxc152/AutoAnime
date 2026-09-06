/*
 * 测试工具:统一 Provider 包裹(EventStreamProvider + MemoryRouter)。
 * 测试始终跑在 mock 模式(api/index 的 isMockMode 在 vitest 下为 dev 默认开),
 * 用 resetMockState() 在每个用例前复位 fixtures。
 *
 * 路由用 createMemoryRouter(data router)而非 MemoryRouter:
 * 支持 useBlocker/useLoader 等 data API,与 App.tsx 的 createHashRouter 形态对齐。
 */
import type { ReactElement } from 'react'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { EventStreamProvider } from '../hooks/EventStreamProvider'
import type { EventSourceFactory, EventSourceHandle, SseMessage } from '../api/sse'

export interface RenderOptions {
  /** 注入受控 SSE 工厂(事件由测试手动 emit) */
  factory?: EventSourceFactory
  /** 初始路由路径(默认 /) */
  initialPath?: string
}

export function renderPage(ui: ReactElement, options: RenderOptions = {}) {
  const { initialPath = '/' } = options

  // 单一 catch-all 路由:任何路径都渲染传入的 ui
  const router = createMemoryRouter(
    [{ path: '*', element: <EventStreamProvider factory={options.factory}>{ui}</EventStreamProvider> }],
    { initialEntries: [initialPath] },
  )

  return render(<RouterProvider router={router} />)
}

/** 测试用可控 EventSource:手动 emit 事件 / 触发错误 */
export class FakeEventSource implements EventSourceHandle {
  private messageHandlers: Array<(message: SseMessage) => void> = []
  private openHandlers: Array<() => void> = []
  private errorHandlers: Array<() => void> = []
  readonly urls: string[] = []
  closed = false

  constructor(url: string) {
    this.urls.push(url)
  }

  emit(message: SseMessage): void {
    for (const handler of this.messageHandlers) handler(message)
  }

  fail(): void {
    for (const handler of this.errorHandlers) handler()
  }

  open(): void {
    for (const handler of this.openHandlers) handler()
  }

  close(): void {
    this.closed = true
  }

  onOpen(cb: () => void): void {
    this.openHandlers.push(cb)
  }

  onMessage(cb: (message: SseMessage) => void): void {
    this.messageHandlers.push(cb)
  }

  onError(cb: () => void): void {
    this.errorHandlers.push(cb)
  }
}

/** 测试用 SSE 帧:data 载荷对齐后端(只含 category/message/payload,无 ts/id) */
export function sseMessage(event: {
  id?: string
  category: string
  message: string
  payload?: Record<string, unknown>
}): SseMessage {
  return {
    data: JSON.stringify({ category: event.category, message: event.message, payload: event.payload ?? {} }),
    lastEventId: event.id ?? '1',
  }
}
