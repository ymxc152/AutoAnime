/*
 * API 契约类型 —— E2 端点的「前端假设」单一真源。
 * E2 合并后对齐时,逐条核对这里;偏差记录进 E3 最终报告的契约疑点清单。
 *
 * 通用约定(Plan §4):
 *  - 分页一律 ?limit=&offset=,响应 { items, total }
 *  - 认证:AUTOANIME_API_TOKEN 非空时校验 X-API-Token 头
 *  - SSE:GET /api/events,token 与 last_event_id 走 query param(EventSource 无法带自定义头)
 */

// ---------- 通用 ----------

export interface Page<T> {
  items: T[]
  total: number
}

/** 分页查询约定(Plan §4:limit/offset) */
export type PageQuery = {
  limit?: number
  offset?: number
}

// ---------- Dashboard:GET /api/metrics ----------

/** 三级管线命中计数 */
export interface LevelHits {
  total: number
  l1_high: number
  l2_hit: number
  l3_entered: number
  llm_calls: number
}

/** 近 7 日曲线单点 */
export interface MetricsDailyPoint {
  date: string // YYYY-MM-DD
  archived: number
  llm_calls: number
  pending: number
}

export interface Metrics {
  /** 人工介入率 = manual 纠正事件数 / 总归档事件数(E1 口径) */
  manual_intervention_rate: number
  /** 本周归档集数 */
  weekly_archived: number
  /** LLM 调用率 = llm_calls / total */
  llm_call_rate: number
  /** 待确认队列当前长度 */
  pending_count: number
  levels: LevelHits
  /** 近 7 日,按日期升序 */
  weekly_curve: MetricsDailyPoint[]
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
  quality_score: number | null
  upgraded_count: number
  /** ISO date YYYY-MM-DD(JST 放送日,展示层转本地) */
  air_date: string | null
  file_path: string | null
}

export interface SeasonDto {
  id: number
  series_id: number
  number: number
  state: SeasonState
  episodes: EpisodeDto[]
}

export interface SeriesDto {
  id: number
  title_cn: string | null
  title_jp: string | null
  title_romaji: string | null
  media_type: MediaType
  status: string
  bangumi_id: string | null
  tmdb_id: string | null
  fansub_pref: string | null
  quality_pref: string | null
  seasons: SeasonDto[]
}

export type SeriesQuery = PageQuery & {
  /** 标题模糊匹配(命中 cn/jp/romaji 任意一者) */
  q?: string
}

// ---------- Pending:GET /api/pending,POST /{id}/confirm|correct|reject ----------

export type PendingStatus = 'pending' | 'resolved' | 'skipped'

/** 单字段解析值的证据来源(Plan §5:name/folder/memory/llm) */
export type EvidenceSource = 'name' | 'folder' | 'memory' | 'llm'

/** 单字段:值 + 证据来源 + 置信度 */
export interface ParsedField<T = string | number | null> {
  value: T
  source: EvidenceSource
  confidence: 'high' | 'medium' | 'low'
}

/** 候选匹配(可选,辅助人工判断) */
export interface PendingCandidate {
  series_id: number
  title: string
  score: number
}

export interface PendingItemDto {
  id: number
  raw_name: string
  /** 停留阶段:parse / organize / download … */
  stage: string
  reason: string | null
  status: PendingStatus
  created_at: string // ISO datetime
  resolved_at: string | null
  /** 逐字段解析结果;evidence 来源标注就在这里 */
  parsed: {
    title: ParsedField<string | null>
    season: ParsedField<number | null>
    episode: ParsedField<number | null>
    fansub: ParsedField<string | null>
    resolution: ParsedField<string | null>
  }
  candidates: PendingCandidate[]
}

export type PendingQuery = PageQuery & {
  status?: PendingStatus
}

/** POST /api/pending/{id}/correct 请求体:仅提交被纠正的字段 */
export interface PendingCorrectBody {
  title?: string
  season?: number
  episode?: number
  fansub?: string
  resolution?: string
}

// ---------- Logs:GET /api/audit,POST /api/organize/{id}/rollback ----------

export type AuditActor = 'auto' | 'manual'

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
  created_at: string // ISO datetime
}

export type AuditQuery = PageQuery & {
  /** 按 operation_id 过滤(可选) */
  operation_id?: string
}

/**
 * POST /api/organize/{id}/rollback
 * 契约假设:{id} 是 organize 类操作的 operation_id(字符串)。
 */
export interface RollbackResult {
  ok: boolean
  operation_id: string
}

// ---------- Subscriptions:GET/POST/DELETE /api/subscriptions ----------

export interface SubscriptionDto {
  id: number
  series_id: number
  title: string // title_cn → romaji 回退后的展示标题
  season_id: number
  season_number: number
  state: SeasonState
  fansub_pref: string | null
  /** 全季集数(预生成集表) */
  episodes_total: number
  /** 已放送集数(JST air_date 已过数) */
  episodes_aired: number
  /** 已归档集数 */
  episodes_collected: number
  next_air_date: string | null
  /** COLLECTED 降频标记(每月仅检查洗版) */
  reduced_frequency: boolean
  enabled: boolean
}

export interface SubscriptionCreateBody {
  /** Mikan RSS 地址(token 可含在 URL 内) */
  rss_url: string
  fansub?: string
}

// ---------- RSS Sources:GET/POST/PATCH/DELETE /api/rss_sources ----------

export interface RssSourceDto {
  id: number
  url: string
  /** 前端永不回显 token;仅是否已配置 */
  has_token: boolean
  season_id: number | null
  enabled: boolean
  last_polled_at: string | null
}

export interface RssSourceCreateBody {
  url: string
  season_id?: number
  /** 可选,单独传 token(SecretStr,不进日志) */
  token?: string
}

export interface RssSourceUpdateBody {
  enabled?: boolean
  url?: string
}

// ---------- Settings:GET/PUT /api/settings ----------

export type DownloadClient = 'qbittorrent' | 'aria2'

/** 自主权限三档 */
export type AutonomyLevel = 'low' | 'medium' | 'high'

export interface SettingsDto {
  downloader: {
    client: DownloadClient
    /** 下载器 WebUI/API 地址 */
    url: string
  }
  llm: {
    enabled: boolean
    model: string
  }
  autonomy: AutonomyLevel
  quality: {
    /** 新分 ≥ 现分 + 阈值才洗版 */
    upgrade_threshold: number
    max_upgrades_per_episode: number
    /** 跨盘降级策略:copy 允许降级;strict 永不 copy */
    copy_policy: 'copy' | 'strict'
    /** 单文件超过此大小跳过洗版(GB) */
    skip_size_gb: number
  }
  naming: {
    template: string
    title_language: 'title_cn' | 'romaji'
  }
}

// ---------- SSE:GET /api/events ----------

/** 事件分类 = autoanime.core.events.EventCategory 透传 */
export type SseCategory = 'parse' | 'download' | 'organize' | 'error' | 'notify' | 'system'

/**
 * SSE data 载荷假设:
 *   event: <category>
 *   id: <自增事件 id,Last-Event-ID 依据>
 *   data: {"message": string, "payload": object, "ts": ISO datetime}
 */
export interface SseEvent {
  id: string | null
  category: SseCategory
  message: string
  payload: Record<string, unknown>
  ts: string
}
