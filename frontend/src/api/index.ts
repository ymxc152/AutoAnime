/*
 * 数据层统一出口:页面只 import { api } 与 { eventSourceFactory }。
 * mock 开关(Plan §5.5「E2 未合并期间 mock 开发,合并后关」):
 *   - VITE_USE_MOCK=1/0 强制;缺省 dev 开、生产构建关
 *   - localStorage 'autoanime-use-mock' 可运行时覆盖(demo 用)
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
    /* 存储不可用时按环境判定 */
  }
  return import.meta.env.DEV
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
