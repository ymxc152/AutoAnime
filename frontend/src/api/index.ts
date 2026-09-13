/*
 * 数据层统一出口:页面只 import { api } 与 { eventSourceFactory }。
 * mock 开关(Plan §5.5「E2 未合并期间 mock 开发,合并后关」):
 *   - 缺省连真实 API(dev 与生产一致,不再默认假数据);仅显式 VITE_USE_MOCK=1
 *     才进入 mock 演示模式
 *   - VITE_USE_MOCK=1/0 强制(环境变量优先);localStorage 'autoanime-use-mock'
 *     可运行时覆盖(demo 用)
 */
import * as realEndpoints from './endpoints'
import { createMockApi } from '../mocks/handlers'
import { mockEventSourceFactory } from '../mocks/sse'
import { nativeEventSourceFactory } from './sse'
import type { EventSourceFactory } from './sse'

export type ApiShape = typeof realEndpoints.endpoints

function resolveUseMock(): boolean {
  const envFlag = import.meta.env.VITE_USE_MOCK
  if (envFlag === '1') return true
  if (envFlag === '0') return false
  try {
    const override = localStorage.getItem('autoanime-use-mock')
    if (override === '1') return true
    if (override === '0') return false
  } catch {
    /* 存储不可用时按缺省判定 */
  }
  // 缺省连真实 API:裸跑 npm run dev 不再整站展示 mock 假数据
  return false
}

export const isMockMode = resolveUseMock()

export const api: ApiShape = isMockMode ? createMockApi() : realEndpoints.endpoints

export const eventSourceFactory: EventSourceFactory = isMockMode
  ? mockEventSourceFactory()
  : nativeEventSourceFactory

export { ApiError, getApiToken, setApiToken } from './client'
export { buildEventsUrl } from './sse'
export type { EventSourceFactory, EventSourceHandle, SseMessage } from './sse'
export * from './types'
