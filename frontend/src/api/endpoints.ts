/*
 * 端点客户端 —— 与 E2 后端真实路由(autoanime/web/routers)一一对应。
 * 对齐后的方法清单同时作为「前端声明的端点契约」真源,
 * 供离线集成冒烟逐条核对(存在/方法/形状)。
 */
import { request } from './client'
import type {
  AuditDto,
  AuditQuery,
  Metrics,
  OperationGroupDto,
  Page,
  PendingCorrectBody,
  PendingItemDto,
  PendingQuery,
  PendingResolveOut,
  RollbackResult,
  RssSourceCreateBody,
  RssSourceDto,
  RssSourceUpdateBody,
  SeriesDto,
  SeriesQuery,
  SettingsDto,
  SettingsUpdateBody,
  SubscriptionCreateBody,
  SubscriptionDto,
} from './types'

export const endpoints = {
  /** GET /api/metrics —— Dashboard 指标(MetricsOut) */
  metrics: {
    get: () => request<Metrics>('/api/metrics'),
  },

  /** GET /api/series —— Library(series 列表,内嵌 season/episode 全树;无 q 过滤) */
  series: {
    list: (query: SeriesQuery = {}) => request<Page<SeriesDto>>('/api/series', { query }),
    /** GET /api/series/{id}/poster —— 本地库海报直读(404 = 无海报,前端降级);
     *  注意:<img> 无法携带 X-API-Token 头,配置 token 时此端点会 401 → 前端降级 */
    posterUrl: (id: number) => `/api/series/${id}/poster`,
  },

  /** /api/pending —— 待确认队列(确认/纠正/拒绝,响应 PendingResolveOut) */
  pending: {
    list: (query: PendingQuery = {}) => request<Page<PendingItemDto>>('/api/pending', { query }),
    confirm: (id: number) =>
      request<PendingResolveOut>(`/api/pending/${id}/confirm`, { method: 'POST' }),
    correct: (id: number, body: PendingCorrectBody) =>
      request<PendingResolveOut>(`/api/pending/${id}/correct`, { method: 'POST', body }),
    reject: (id: number) =>
      request<PendingResolveOut>(`/api/pending/${id}/reject`, { method: 'POST' }),
  },

  /** GET /api/audit —— Logs 明细(可按 operation_id/entity/action 过滤) */
  audit: {
    list: (query: AuditQuery = {}) => request<Page<AuditDto>>('/api/audit', { query }),
  },

  /** GET /api/audit/operations —— 后端按 operation_id 分组视图(Logs 组列表) */
  auditOperations: {
    list: (query: { limit?: number; offset?: number } = {}) =>
      request<Page<OperationGroupDto>>('/api/audit/operations', { query }),
  },

  /** POST /api/organize/{audit_id}/rollback —— {id} 是数值 audit 行 id */
  organize: {
    rollback: (auditId: number) =>
      request<RollbackResult>(`/api/organize/${auditId}/rollback`, { method: 'POST' }),
  },

  /** /api/subscriptions —— 订阅(载体 series 行;POST 至少一个标题+预生成集表) */
  subscriptions: {
    list: (query: { limit?: number; offset?: number } = {}) =>
      request<Page<SubscriptionDto>>('/api/subscriptions', { query }),
    create: (body: SubscriptionCreateBody) =>
      request<SubscriptionDto>('/api/subscriptions', { method: 'POST', body }),
    remove: (id: number) => request<void>(`/api/subscriptions/${id}`, { method: 'DELETE' }),
  },

  /** /api/rss_sources —— RSS 源 CRUD(启停 = PATCH enabled;season_id 创建必填) */
  rssSources: {
    list: (query: { limit?: number; offset?: number } = {}) =>
      request<Page<RssSourceDto>>('/api/rss_sources', { query }),
    create: (body: RssSourceCreateBody) =>
      request<RssSourceDto>('/api/rss_sources', { method: 'POST', body }),
    update: (id: number, body: RssSourceUpdateBody) =>
      request<RssSourceDto>(`/api/rss_sources/${id}`, { method: 'PATCH', body }),
    remove: (id: number) => request<void>(`/api/rss_sources/${id}`, { method: 'DELETE' }),
  },

  /** GET/PUT /api/settings —— 运行时设置(PUT 白名单覆写,进程内生效) */
  settings: {
    get: () => request<SettingsDto>('/api/settings'),
    update: (body: SettingsUpdateBody) => request<SettingsDto>('/api/settings', { method: 'PUT', body }),
  },
}
