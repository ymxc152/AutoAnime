/*
 * API 契约类型 —— 已对齐 E2 后端真实实现(autoanime/web/schemas.py)。
 * E2↔E3 对齐原则:前端适配后端,不再保留「契约假设」。
 *
 * 通用约定(后端 web/deps.py + schemas.py):
 *  - 分页一律 ?limit=&offset=,响应 Page 信封 { total, limit, offset, items }
 *  - 认证:AUTOANIME_API_TOKEN 非空时校验 X-API-Token 头(SSE 允许 ?token=)
 *  - SSE:GET /api/events,id 为 audit 行 id,last_event_id 走 query 兜底
 */

// ---------- 通用 ----------

/** 后端统一分页信封(schemas.Page[ItemT]) */
export interface Page<T> {
  total: number
  limit: number
  offset: number
  items: T[]
}

/** 分页查询约定(Plan §4:limit/offset) */
export type PageQuery = {
  limit?: number
  offset?: number
}

// ---------- Dashboard:GET /api/metrics ----------

/** 单级管线统计:total=该级解析事件数,llm_called=其中调用 LLM 的次数 */
export interface LevelStats {
  level: number
  total: number
  llm_called: number
  /** 按 outcome 细分的计数(如 l1_high/memory_hit/low_confidence) */
  outcomes: Record<string, number>
}

/** LLM 调用周曲线单点:bucket=ISO 周(如 2026-W36) */
export interface CurvePoint {
  bucket: string
  total: number
  llm_called: number
  llm_rate: number | null
}

/** 待确认 28 天趋势单点:bucket=YYYY-MM-DD */
export interface PendingTrendPoint {
  bucket: string
  created: number
  resolved: number
}

/** 解析记忆来源分布(source×status 行数) */
export interface MemorySourceStats {
  source: string
  status: string
  rows: number
}

/** GET /api/metrics 响应(= 后端 MetricsOut,字段一一对应) */
export interface Metrics {
  /** 人工介入率 = manual 审计行 / 总审计行;无审计行时为 null */
  intervention_rate: number | null
  audit_total: number
  audit_manual: number
  by_level: LevelStats[]
  /** 最近 8 个 ISO 周(含当周)的 LLM 调用曲线 */
  llm_call_curve_weekly: CurvePoint[]
  /** 最近 28 天待确认创建/解决趋势 */
  pending_trend_daily: PendingTrendPoint[]
  /** 待确认队列当前长度 */
  pending_open: number
  /** 库内集状态分布(如 {missing: 12, organized: 40}) */
  episode_states: Record<string, number>
  memory_sources: MemorySourceStats[]
}

// ---------- Library:GET /api/series ----------

export type EpisodeState =
  | 'missing'
  | 'downloading'
  | 'downloaded'
  | 'organized'
  | 'upgraded'
  | 'ignored'

export type SeasonState = 'upcoming' | 'airing' | 'ended' | 'collected'

export type MediaType = 'tv' | 'movie' | 'ova' | 'special'

export interface EpisodeDto {
  id: number
  series_id: number
  season_id: number | null
  number: number
  state: EpisodeState
  upgraded_count: number
  quality_score: number | null
  air_date: string | null
  file_path: string | null
  file_hash: string | null
}

export interface SeasonDto {
  id: number
  series_id: number
  number: number
  /** 后端字段名是 status(SeasonOut.status) */
  status: SeasonState
  episodes: EpisodeDto[]
}

export interface SeriesDto {
  id: number
  title_cn: string | null
  title_jp: string | null
  title_romaji: string | null
  media_type: MediaType
  tmdb_id: string | null
  bangumi_id: string | null
  fansub_pref: string | null
  quality_pref: string | null
  status: string
  seasons: SeasonDto[]
}

/** 后端 GET /api/series 不支持标题过滤(q),搜索在前端做 */
export type SeriesQuery = PageQuery

// ---------- Pending:GET /api/pending,POST /{id}/confirm|correct|reject ----------

export type PendingStatus = 'pending' | 'resolved' | 'skipped'

/**
 * GET /api/pending 单行(= 后端 PendingOut)。
 * context 是识别管线写入的草稿字段(title/season/episode/segment/fansub/
 * folder/parent_path);resolution 存侧是 JSON 字符串、读侧已解析为对象。
 * 后端不提供逐字段证据来源/置信度,抽屉视图按可 absence 渲染。
 */
export interface PendingItemDto {
  id: number
  raw_name: string
  context: Record<string, unknown>
  stage: string
  reason: string | null
  status: PendingStatus
  resolution: Record<string, unknown> | string | null
  resolved_by: string | null
  created_at: string
  resolved_at: string | null
}

export type PendingQuery = PageQuery & {
  status?: PendingStatus
}

/**
 * POST /api/pending/{id}/correct 请求体(= 后端 PendingCorrectIn)。
 * title 必填(纠正的核心是剧名归属)——前端提交时始终带上当前 title。
 * season/episode/segment/fansub 可选;缺省字段回退行内 context 草稿。
 */
export interface PendingCorrectBody {
  title: string
  season?: number
  episode?: number
  segment?: string
  fansub?: string
}

/** confirm/correct/reject 的统一响应(= 后端 PendingResolveOut) */
export interface PendingResolveOut {
  id: number
  status: PendingStatus
  resolution: Record<string, unknown> | null
  resolved_by: string
  learned_entries: number
  bypassed: boolean
}

// ---------- Logs:GET /api/audit、/api/audit/operations,POST /api/organize/{id}/rollback ----------

export type AuditActor = 'auto' | 'manual'

/** GET /api/audit 单行(= 后端 AuditOut;注意:没有 created_at,组内排序按 id) */
export interface AuditDto {
  id: number
  operation_id: string
  entity: string
  entity_id: number | null
  action: string
  /** 正向指令 JSON(如归档路径映射) */
  instruction: Record<string, unknown>
  /** 逆向指令 JSON(rollback 依据) */
  reverse: Record<string, unknown>
  actor: AuditActor
}

export type AuditQuery = PageQuery & {
  operation_id?: string
  entity?: string
  action?: string
}

/**
 * GET /api/audit/operations 单组(= 后端 OperationGroupOut):
 * 后端已按 operation_id 分好组,最新组在前;展开明细再查 /api/audit?operation_id=。
 */
export interface OperationGroupDto {
  operation_id: string
  rows: number
  entities: string[]
  actions: string[]
  first_audit_id: number
  last_audit_id: number
  /** 后端按组内最新 audit 行是否带 reverse 判定;false 时 UI 隐藏撤销 */
  rollbackable: boolean
}

/**
 * POST /api/organize/{id}/rollback
 * {id} 是数值 audit 行 id(不是 operation_id 字符串);组级撤销取该组最新
 * 一条 audit 行 id(last_audit_id)。404=行不存在,409=行无 reverse 指令。
 * 响应 = 后端 RollbackOut。
 */
export interface RollbackResult {
  audit_id: number
  operation_id: string
  /** applied/skipped 明细(reverse 指令执行结果,诚实契约) */
  applied: { applied: Record<string, unknown>; skipped: Record<string, unknown> }
  learned: boolean
}

// ---------- Subscriptions:GET/POST/PATCH/DELETE /api/subscriptions ----------

/** 订阅行的单季放送进度(= 后端 SeasonProgressOut) */
export interface SeasonProgressDto {
  season_id: number
  number: number
  status: SeasonState
  episodes_total: number
  episodes_missing: number
  episodes_organized: number
  rss_sources: number
}

/** 订阅行(= 后端 SubscriptionOut):载体是 series 行 + 预生成季/集表 */
export interface SubscriptionDto {
  id: number
  title_cn: string | null
  title_jp: string | null
  title_romaji: string | null
  media_type: MediaType
  status: string
  fansub_pref: string | null
  quality_pref: string | null
  seasons: SeasonProgressDto[]
}

/**
 * POST /api/subscriptions 请求体(= 后端 SubscriptionCreateIn):
 * title_cn/title_jp/title_romaji 至少一个;episode_count 非空时预生成
 * N 条 MISSING 集(ARCHITECTURE §2)。RSS 地址关联走 /api/rss_sources。
 */
export interface SubscriptionCreateBody {
  title_cn?: string
  title_jp?: string
  title_romaji?: string
  media_type?: MediaType
  season_number?: number
  episode_count?: number | null
  fansub_pref?: string | null
  quality_pref?: string | null
}

// ---------- RSS Sources:GET/POST/PATCH/DELETE /api/rss_sources ----------

/** RSS 源行(= 后端 RssSourceOut):独立 token 不回显,只回 has_token;URL 内嵌 token 按明文 URL 展示 */
export interface RssSourceDto {
  id: number
  url: string
  has_token: boolean
  /** 外键指向 season.id,非空 */
  season_id: number
  enabled: boolean
  last_polled_at: string | null
}

/** POST /api/rss_sources 请求体(= 后端 RssSourceCreateIn):season_id 必填 */
export interface RssSourceCreateBody {
  url: string
  season_id: number
  token?: string
  enabled?: boolean
}

/** PATCH /api/rss_sources/{id} 请求体(= 后端 RssSourceUpdateIn):url/token/enabled 局部更新 */
export interface RssSourceUpdateBody {
  url?: string
  /** 显式传 null = 清除 token */
  token?: string | null
  enabled?: boolean
}

// ---------- Settings:GET/PUT /api/settings ----------

/**
 * GET /api/settings 响应(= 后端 SettingsOut,扁平结构):
 * 密钥只回 has_* 布尔;quality/naming 段 v1 暂缺(无持久化 settings 表,
 * E4 后才有),UI 不渲染假数据。
 */
export interface SettingsDto {
  dry_run: boolean
  l2_enabled: boolean
  llm_enabled: boolean
  llm_model: string | null
  reference_enabled: boolean
  reference_order: string[]
  library_path: string
  download_path: string
  api_host: string
  api_port: number
  api_cors_dev_origins: string[]
  api_sse_heartbeat_s: number
  api_sse_replay_limit: number
  has_api_token: boolean
  has_llm_api_key: boolean
}

/** PUT /api/settings 请求体(= 后端 SettingsUpdateIn):白名单运行时覆写,重启后回 env/toml 值 */
export interface SettingsUpdateBody {
  dry_run?: boolean
  l2_enabled?: boolean
  llm_enabled?: boolean
  llm_model?: string
  reference_enabled?: boolean
  reference_order?: string[]
}

// ---------- SSE:GET /api/events ----------

/** 事件分类 = autoanime.core.events.EventCategory 透传 */
export type SseCategory = 'parse' | 'download' | 'organize' | 'error' | 'notify' | 'system'

/**
 * SSE 事件(对齐后端 web/sse.py):
 *   retry:3000 → id:{audit_id} → event:{category} → data:{category,message,payload}
 * 后端 data 载荷不含 id/ts:id 走 SSE id: 行(= audit 行 id,Last-Event-ID 依据),
 * ts 由前端在接收时刻本地生成(仅用于展示排序,不代表服务端时间)。
 */
export interface SseEvent {
  id: string | null
  category: SseCategory
  message: string
  payload: Record<string, unknown>
  /** 前端接收时刻本地生成(后端不发) */
  ts: string
}
