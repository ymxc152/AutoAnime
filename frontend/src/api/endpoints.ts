/*
 * 端点客户端 —— 与 E2 后端真实路由(autoanime/web/routers)一一对应。
 * 对齐后的方法清单同时作为「前端声明的端点契约」真源,
 * 供离线集成冒烟逐条核对(存在/方法/形状)。
 */
import { request } from './client'
import type {
  AuditDto,
  AuditQuery,
  ConfirmNameBody,
  ConfirmNameOut,
  EpisodeReparseBody,
  EpisodeReparseOut,
  FilesystemListing,
  Metrics,
  OperationGroupDto,
  Page,
  ParsePreviewBody,
  ParsePreviewResponse,
  PipelineImportBody,
  PipelineTask,
  PendingConfirmBody,
  PendingCorrectBody,
  PendingItemDto,
  PendingQuery,
  PendingRejectBody,
  PendingResolveOut,
  ReportOut,
  RollbackResult,
  RssPollResult,
  RssSourceCreateBody,
  RssSourceDto,
  RssSourceUpdateBody,
  SchedulerRunResponse,
  SchedulerScope,
  MikanGroupsDto,
  RssPreviewBody,
  RssPreviewResponse,
  SeasonBrowseOut,
  SeasonName,
  SeriesDto,
  SeriesQuery,
  NotifyTestOut,
  QbitTestOut,
  SettingsDto,
  SettingsUpdateBody,
  SettingsUpdateOut,
  SubscriptionCreateBody,
  SubscriptionDto,
  SubscriptionUpdateBody,
} from './types'

export const endpoints = {
  /** GET /api/metrics —— Dashboard 指标(MetricsOut) */
  metrics: {
    get: () => request<Metrics>('/api/metrics'),
  },

  /** GET /api/report —— 12-F 识别指标(CLI report --json 同构,纯读) */
  report: {
    get: () => request<ReportOut>('/api/report'),
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
    // 12-F:confirm 支持可选 PendingConfirmIn 覆写字段(不传 = 直采 context 草稿,
    // 后端 body: PendingConfirmIn | None = None);correct 已带覆写,不变。
    confirm: (id: number, body?: PendingConfirmBody) =>
      request<PendingResolveOut>(`/api/pending/${id}/confirm`, { method: 'POST', body }),
    correct: (id: number, body: PendingCorrectBody) =>
      request<PendingResolveOut>(`/api/pending/${id}/correct`, { method: 'POST', body }),
    // 12-F:reject 支持可选 reason(后端 PendingRejectIn.reason;不传 = 不填原因)
    reject: (id: number, body?: PendingRejectBody) =>
      request<PendingResolveOut>(`/api/pending/${id}/reject`, { method: 'POST', body }),
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
    update: (id: number, body: SubscriptionUpdateBody) =>
      request<SubscriptionDto>(`/api/subscriptions/${id}`, { method: 'PATCH', body }),
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
    // 12-F:行内立即轮询单个源(409 = 源停用或已有轮询进行中)
    poll: (id: number) =>
      request<RssPollResult>(`/api/rss_sources/${id}/poll`, { method: 'POST' }),
  },

  /** P1-E:GET /api/season-calendar —— 当季选番(Bangumi 时间表;拉取失败 degraded=true 空表,不 500) */
  seasonCalendar: {
    get: () => request<SeasonBrowseOut>('/api/season-calendar'),
  },

  /** P1-E:GET /api/season-browse?year=&season= —— 历史季浏览(同构出参;year 上限 = 当前年 + 1) */
  seasonBrowse: {
    get: (query: { year: number; season: SeasonName }) =>
      request<SeasonBrowseOut>('/api/season-browse', { query }),
  },

  /** GET /api/mikan/subtitle_groups?title= —— Mikan 字幕组 RSS 发现(选番抽屉「自动获取」;404=无命中,502=上游失败) */
  mikan: {
    subtitleGroups: (title: string) =>
      request<MikanGroupsDto>('/api/mikan/subtitle_groups', { query: { title } }),
  },

  /** POST /api/episodes/{id}/reparse —— 12-F 集重新识别(dry_run=true 预览/false 执行) */
  episodes: {
    reparse: (id: number, body: EpisodeReparseBody) =>
      request<EpisodeReparseOut>(`/api/episodes/${id}/reparse`, { method: 'POST', body, timeoutMs: 300_000 }),
  },

  /** Pipeline D:单文件 L1 试跑 / 异步目录导入 / 任务状态 */
  pipeline: {
    parsePreview: (body: ParsePreviewBody) =>
      request<ParsePreviewResponse>('/api/pipeline/parse-preview', { method: 'POST', body, timeoutMs: 300_000 }),
    startImport: (body: PipelineImportBody) =>
      request<{ task_id: string; status: 'running' }>('/api/pipeline/import', { method: 'POST', body }),
    task: (id: string) => request<PipelineTask>(`/api/pipeline/tasks/${id}`),
    // 12-F:库外人工确认命名(学习三件套 + pending 收尾 + 归档;422 = 校验失败)
    confirmName: (body: ConfirmNameBody) =>
      request<ConfirmNameOut>('/api/pipeline/confirm-name', { method: 'POST', body }),
    // 通用 RSS 匹配预览(选番抽屉):拉 feed 逐条试判,零落库;502=拉取失败
    rssPreview: (body: RssPreviewBody) =>
      request<RssPreviewResponse>('/api/pipeline/rss-preview', { method: 'POST', body }),
  },

  /** GET /api/filesystem —— 目录浏览(P1-D):path 缺省 = 盘符根视图;include_files 列文件(文件选择模式) */
  filesystem: {
    list: (path?: string, includeFiles?: boolean) =>
      request<FilesystemListing>('/api/filesystem', {
        query: { path, ...(includeFiles ? { include_files: true } : {}) },
      }),
  },

  /** Scheduler D:手动触发一轮订阅闭环 */
  scheduler: {
    runOnce: (body: { scope?: SchedulerScope } = {}) =>
      request<SchedulerRunResponse>('/api/scheduler/run-once', { method: 'POST', body }),
  },

  /** GET/PUT /api/settings —— 配置中心(12-D 三档生效;PUT 响应带 applied/warnings) */
  settings: {
    get: () => request<SettingsDto>('/api/settings'),
    update: (body: SettingsUpdateBody) =>
      request<SettingsUpdateOut>('/api/settings', { method: 'PUT', body }),
    // 12-E:测试动作按「运行时 + DB 覆盖」的合并配置试跑,不改任何配置
    notifyTest: () => request<NotifyTestOut>('/api/settings/notify-test', { method: 'POST' }),
    qbitTest: () => request<QbitTestOut>('/api/settings/qbit-test', { method: 'POST' }),
  },
}
