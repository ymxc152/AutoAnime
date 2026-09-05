/*
 * 端点客户端 —— 与 Plan §4(E2 规格)一一对应。
 * 每个方法 = 一条契约假设;E2 合并后按此逐条对齐。
 */
import { request } from './client'
import type {
  AuditDto,
  AuditQuery,
  Metrics,
  Page,
  PendingCorrectBody,
  PendingItemDto,
  PendingQuery,
  RollbackResult,
  RssSourceCreateBody,
  RssSourceDto,
  RssSourceUpdateBody,
  SeriesDto,
  SeriesQuery,
  SettingsDto,
  SubscriptionCreateBody,
  SubscriptionDto,
} from './types'

export const endpoints = {
  /** GET /api/metrics —— Dashboard 指标 */
  metrics: {
    get: () => request<Metrics>('/api/metrics'),
  },

  /** GET /api/series —— Library(series 列表,内嵌 season/episode 树) */
  series: {
    list: (query: SeriesQuery = {}) => request<Page<SeriesDto>>('/api/series', { query }),
  },

  /** /api/pending —— 待确认队列(确认/纠正/拒绝) */
  pending: {
    list: (query: PendingQuery = {}) => request<Page<PendingItemDto>>('/api/pending', { query }),
    confirm: (id: number) =>
      request<PendingItemDto>(`/api/pending/${id}/confirm`, { method: 'POST' }),
    correct: (id: number, body: PendingCorrectBody) =>
      request<PendingItemDto>(`/api/pending/${id}/correct`, { method: 'POST', body }),
    reject: (id: number) => request<PendingItemDto>(`/api/pending/${id}/reject`, { method: 'POST' }),
  },

  /** GET /api/audit —— Logs(审计日志分页,按 operation_id 分组在前端做) */
  audit: {
    list: (query: AuditQuery = {}) => request<Page<AuditDto>>('/api/audit', { query }),
  },

  /** POST /api/organize/{operation_id}/rollback —— 撤销整理 */
  organize: {
    rollback: (operationId: string) =>
      request<RollbackResult>(`/api/organize/${encodeURIComponent(operationId)}/rollback`, {
        method: 'POST',
      }),
  },

  /** /api/subscriptions —— 订阅 CRUD */
  subscriptions: {
    list: (query: { limit?: number; offset?: number } = {}) =>
      request<Page<SubscriptionDto>>('/api/subscriptions', { query }),
    create: (body: SubscriptionCreateBody) =>
      request<SubscriptionDto>('/api/subscriptions', { method: 'POST', body }),
    remove: (id: number) => request<void>(`/api/subscriptions/${id}`, { method: 'DELETE' }),
  },

  /** /api/rss_sources —— RSS 源 CRUD(启停 = PATCH enabled) */
  rssSources: {
    list: (query: { limit?: number; offset?: number } = {}) =>
      request<Page<RssSourceDto>>('/api/rss_sources', { query }),
    create: (body: RssSourceCreateBody) =>
      request<RssSourceDto>('/api/rss_sources', { method: 'POST', body }),
    update: (id: number, body: RssSourceUpdateBody) =>
      request<RssSourceDto>(`/api/rss_sources/${id}`, { method: 'PATCH', body }),
    remove: (id: number) => request<void>(`/api/rss_sources/${id}`, { method: 'DELETE' }),
  },

  /** GET/PUT /api/settings —— 运行时设置 */
  settings: {
    get: () => request<SettingsDto>('/api/settings'),
    update: (body: SettingsDto) => request<SettingsDto>('/api/settings', { method: 'PUT', body }),
  },
}
