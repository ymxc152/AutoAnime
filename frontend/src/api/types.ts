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
export type SeriesQuery = PageQuery & {
  /** 后端标题关键词:cn/jp/romaji 任一命中 */
  q?: string
}

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

/**
 * 12-F:POST /api/pending/{id}/confirm 请求体(= 后端 PendingConfirmIn,
 * body 整体可选)。字段缺省时后端回退行内 context 草稿;前端「空字段不传」
 * 即等价于无 body 直采。快速确认(表格行按钮)保持不带 body。
 */
export interface PendingConfirmBody {
  title?: string
  season?: number
  episode?: number
  segment?: string
  fansub?: string
}

/**
 * 12-F:POST /api/pending/{id}/reject 请求体(= 后端 PendingRejectIn,
 * body 整体可选)。reason 可选——填写时后端记入 resolution.reason
 * (拒绝不学习、不落记忆);留空/不传 = 不填原因。
 */
export interface PendingRejectBody {
  reason?: string
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

/** GET /api/audit 单行(= 后端 AuditOut;created_at 为 0008 迁移后新增,旧行为 null) */
export interface AuditDto {
  id: number
  operation_id: string
  entity: string
  entity_id: number | null
  action: string
  /** 行写入时刻(ISO);0008 迁移前的历史行为 null */
  created_at?: string | null
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
  /** 组内最新行(last_audit_id)的写入时刻(ISO);历史组为 null */
  last_created_at?: string | null
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
  include_keywords: string | null
  exclude_keywords: string | null
  seasons: SeasonProgressDto[]
  /** P0-B:本次(创建)提交的 RSS 是否落库;token/URL 永不回显。列表行恒 false */
  rss_saved?: boolean
  /** P0-B adopt:本次创建命中已有 Series 被收编时为 true(重复订阅不静默) */
  adopted?: boolean
}

/**
 * POST /api/subscriptions 请求体(= 后端 SubscriptionCreateIn):
 * title_cn/title_jp/title_romaji 至少一个;episode_count 非空时预生成
 * N 条 MISSING 集(ARCHITECTURE §2)。RSS 地址关联走 /api/rss_sources。
 */
export interface SubscriptionUpdateBody {
  status?: string
  /** 显式传 null = 清除偏好 */
  fansub_pref?: string | null
  /** 显式传 null = 清除偏好 */
  quality_pref?: string | null
  /** 通用 RSS 规则:分号分隔关键词;显式传 null = 清除 */
  include_keywords?: string | null
  exclude_keywords?: string | null
}

export interface SubscriptionCreateBody {
  title_cn?: string
  title_jp?: string
  title_romaji?: string
  media_type?: MediaType
  season_number?: number
  episode_count?: number | null
  fansub_pref?: string | null
  quality_pref?: string | null
  /** 通用 RSS 规则:分号分隔关键词;include 非空=白名单,exclude 命中=拒绝 */
  include_keywords?: string | null
  exclude_keywords?: string | null
  /** P0-B 一步订阅:Bangumi subject_id 作 adopt 精确键(字符串透传) */
  bangumi_id?: string
  /** P0-B 一步订阅:提供时与订阅同一事务挂 RssSource;响应 rss_saved 表示是否落库 */
  rss_url?: string
  /** P0-B:RSS token 按密钥处理,任何响应不回显 */
  rss_token?: string
}

// ---------- P1-E:季度选番(GET /api/season-calendar、/api/season-browse) ----------

/** 季名枚举(后端 SeasonName Literal;非法值后端 422) */
export type SeasonName = 'winter' | 'spring' | 'summer' | 'fall'

/** 规范化番剧条目(= 后端 routers/calendar.py BangumiItemOut) */
export interface BangumiItemDto {
  subject_id: number
  title_cn: string | null
  title_jp: string
  image_url: string | null
  rating: number | null
  air_date: string | null
  eps: number | null
  mikan_search_url: string
  /** Bangumi 原样平台(TV/OVA/ONA/剧场版…;可空)——特别篇过滤依据 */
  platform: string | null
  /** 地区码 jp/cn/kr/us(tags 推导;可空)——地区过滤依据 */
  region: string | null
}

/**
 * 时间表/季浏览统一出参(= 后端 SeasonBrowseOut):
 * degraded=true 时 items 为空并附 reason(网关降级链,不 500)。
 */
export interface SeasonBrowseOut {
  items: BangumiItemDto[]
  degraded: boolean
  reason: string | null
}

// ---------- Pipeline / Scheduler:D/E 操作入口 ----------

export interface ParsePreviewBody {
  name: string
  folder?: string | null
  parent?: string | null
}

export interface ParsePreviewResponse {
  route: string
  result: {
    title: string
    season: number | null
    episode: number | null
    segment: string
    fansub: string | null
    level: string
    confidence: number
    missing_fields: string[]
    evidence: Record<string, string>
  } | null
}

export interface PipelineImportBody {
  directory: string
  dry_run: boolean
}

export type PipelineTaskStatus = 'running' | 'completed' | 'failed'

export interface PipelineTask {
  task_id: string
  kind: 'import'
  status: PipelineTaskStatus
  directory: string
  dry_run: boolean
  created_at: string
  finished_at: string | null
  processed: number
  total: number | null
  summary: {
    total: number
    scanned: number
    archived: number
    pending: number
    failed: number
    skipped: number
  } | null
  error: string | null
}

export type SchedulerScope = 'all' | 'rss' | 'download'

export interface SchedulerRunResponse {
  scope: SchedulerScope
  reports: Record<string, Record<string, unknown>>
  errors: string[]
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

// ---------- Settings:GET/PUT /api/settings + notify-test / qbit-test ----------

/**
 * GET /api/settings 响应(= 后端 SettingsOut,扁平结构):
 * 密钥只回 has_* 布尔,值永不回显;重启生效档字段 DB 有覆盖时显示
 * 「重启后将生效的值」(后端 GET 按 DB 覆盖优先展示)。
 */
export interface SettingsDto {
  // --- 运行(立即生效档) ---
  dry_run: boolean
  l2_enabled: boolean
  llm_enabled: boolean
  llm_model: string | null
  reference_enabled: boolean
  reference_order: string[]
  llm_timeout_s: number
  llm_max_retries: number
  reference_qps: number | null
  pending_backlog_alert_threshold: number
  log_level: string
  // --- 调度(scheduler_rebuild 档) ---
  scheduler_enabled: boolean
  rss_poll_interval_minutes: number
  rss_poll_jitter_pct: number
  download_poll_interval_s: number
  download_max_retries: number
  collected_check_days: number
  // --- 下载器(requires_restart 档,密钥只回 has_*) ---
  downloader: string
  qbittorrent_host: string
  qbittorrent_port: number
  qbittorrent_username: string
  // --- 通知(requires_restart 档) ---
  notify_enabled: boolean
  notify_telegram_chat_id: string | null
  notify_events: string[]
  // --- 洗版 / 命名 / RSS 抓取(requires_restart 档) ---
  upgrade_threshold: number
  upgrade_max_per_episode: number
  upgrade_copy_policy: string
  upgrade_skip_size_gb: number
  mismatch_backfill_budget: number
  naming_title_language: string
  rss_fetch_timeout_s: number
  rss_fetch_retries: number
  // --- 识别(requires_restart 档:LLM 连接类) ---
  llm_base_url: string | null
  // --- 环境(只读) ---
  library_path: string
  download_path: string
  /** 绝对路径(后端 resolve 后);旧后端缺省,UI 回退 library_path */
  library_path_abs?: string
  download_path_abs?: string
  api_host: string
  api_port: number
  api_cors_dev_origins: string[]
  api_sse_heartbeat_s: number
  api_sse_replay_limit: number
  // --- 密钥 has_* 布尔 ---
  has_api_token: boolean
  has_llm_api_key: boolean
  has_tmdb_api_key: boolean
  has_qbittorrent_password: boolean
  has_notify_webhook_url: boolean
  has_notify_telegram_bot_token: boolean
  // --- 并发写冲突基线(后端 GET 恒返回;mock fixture 由 handlers 补齐,故声明可选) ---
  updated_at?: string | null
}

/**
 * PUT /api/settings 请求体(= 后端 SettingsUpdateIn,39 项白名单,
 * extra=forbid → 白名单外字段 422)。密钥字段语义:空串 = 不修改,
 * 显式 null = 清除(删 DB 覆盖回落 env/toml);非密钥字段不传 = 不修改。
 */
export interface SettingsUpdateBody {
  // 立即生效
  dry_run?: boolean
  l2_enabled?: boolean
  llm_enabled?: boolean
  llm_model?: string
  reference_enabled?: boolean
  reference_order?: string[]
  llm_timeout_s?: number
  llm_max_retries?: number
  reference_qps?: number
  pending_backlog_alert_threshold?: number
  log_level?: string
  // 路径类(立即生效;绝对路径,二者必须不同)
  library_path?: string
  download_path?: string
  // 调度类(重建 loop 生效)
  scheduler_enabled?: boolean
  rss_poll_interval_minutes?: number
  rss_poll_jitter_pct?: number
  download_poll_interval_s?: number
  download_max_retries?: number
  collected_check_days?: number
  // 重启生效(连接/密钥类)
  llm_base_url?: string
  /** 密钥:空串 = 不修改,null = 清除 */
  llm_api_key?: string | null
  /** 密钥:空串 = 不修改,null = 清除 */
  tmdb_api_key?: string | null
  downloader?: string
  qbittorrent_host?: string
  qbittorrent_port?: number
  qbittorrent_username?: string
  /** 密钥:空串 = 不修改,null = 清除 */
  qbittorrent_password?: string | null
  notify_enabled?: boolean
  /** 密钥(后端按 SecretStr 处理):空串 = 不修改,null = 清除 */
  notify_webhook_url?: string | null
  /** 密钥:空串 = 不修改,null = 清除 */
  notify_telegram_bot_token?: string | null
  notify_telegram_chat_id?: string
  notify_events?: string[]
  upgrade_threshold?: number
  upgrade_max_per_episode?: number
  upgrade_copy_policy?: string
  upgrade_skip_size_gb?: number
  mismatch_backfill_budget?: number
  naming_title_language?: string
  rss_fetch_timeout_s?: number
  rss_fetch_retries?: number
  /** 并发写冲突基线:携带 GET 时的 updated_at;与服务端当前值不一致后端回 409 detail=settings_changed */
  base_updated_at?: string | null
}

/** 12-E:PUT 生效三档(后端 SettingEffect Literal 透传) */
export type SettingEffect = 'immediate' | 'scheduler_rebuild' | 'requires_restart'

/**
 * PUT /api/settings 响应(= 后端 SettingsUpdateOut):
 * 常规 SettingsOut 载荷 + 每个被改字段的生效档位 + 调度重建警告。
 */
export interface SettingsUpdateOut extends SettingsDto {
  applied: Record<string, SettingEffect>
  warnings: string[]
}

/** notify-test 逐通道结果(error 只含异常类型名,不含 URL/token) */
export interface ChannelTestResult {
  channel: string
  ok: boolean
  error: string | null
}

/** POST /api/settings/notify-test 响应(= 后端 NotifyTestOut) */
export interface NotifyTestOut {
  results: ChannelTestResult[]
}

/** POST /api/settings/qbit-test 响应(= 后端 QbitTestOut) */
export interface QbitTestOut {
  ok: boolean
  version: string | null
  /** qB 全局默认保存路径(拿不到为 null) */
  save_path: string | null
  error: string | null
}

// ---------- 12-F:RSS 立即轮询 / 人工确认命名 / 集重新识别 / 识别指标 ----------

/**
 * POST /api/rss_sources/{id}/poll 响应(= 后端 rss_sources.py 内联 dict):
 * 立即轮询单个源;download 为同一轮顺带的下载对账摘要。
 */
export interface RssPollResult {
  source_id: number
  season_id: number
  /** 未到计划轮询时间被跳过(非错误) */
  skipped_not_due: boolean
  /** 源拉取失败原因(网络/超时等;轮询本身仍返回 200) */
  fetch_error: string | null
  entries_total: number
  seen: number
  rejected: number
  backlog: number
  /** 拾取并推送下载器的新条目数 */
  picked: number
  gaps: string[]
  reconciled: number
  reconcile_notes: string[]
  download: {
    checked: number
    completed: number
    failed: number
    retried: number
    notes: string[]
  }
}

/** POST /api/pipeline/confirm-name 请求体(= 后端 ConfirmNameIn):name 必填 */
export interface ConfirmNameBody {
  name: string
  title?: string
  season?: number
  episode?: number
  segment?: string
  fansub?: string
}

/** confirm-name 归档结果(= 后端 ArchiveOutcome.as_dict):未归档时 reason 必有 */
export interface ConfirmNameArchive {
  archived: boolean
  dst?: string
  strategy?: string
  reason?: string
}

/** confirm-name 学习三件套单条(= 后端 _confirm_entries_payload 条目) */
export interface ConfirmNameEntry {
  key_level: string
  key_hash: string
  title_shape: string
  source: string
  status: string
  hit_count: number
  corrected_count: number
}

/** POST /api/pipeline/confirm-name 响应(= 后端 ConfirmNameOut) */
export interface ConfirmNameOut {
  bypassed: boolean
  /** 按 raw_name 一并收尾的未决 pending 行数 */
  resolved_pending: number
  archive: ConfirmNameArchive
  entries: ConfirmNameEntry[]
}

/** POST /api/episodes/{id}/reparse 请求体(= 后端 EpisodeReparseIn):true 预览 / false 执行 */
export interface EpisodeReparseBody {
  dry_run: boolean
}

/** reparse 的解析结果(= 后端 _parse_result_to_json,与 parse-preview result 同构) */
export interface EpisodeReparseParsed {
  title: string
  season: number | null
  episode: number | null
  segment: string
  fansub: string | null
  level: string
  confidence: number
  missing_fields: string[]
  evidence: Record<string, string>
}

/** reparse 的归档动作(action=skip 时带 reason,如 D21 守卫命中) */
export interface EpisodeReparseAction {
  /** 目标路径;无移动计划时为 null */
  dst: string | null
  strategy: string
  episode_state: string
  /** skip = 目标位守卫命中不移动;archive = 预览(将归档)/执行(已归档) */
  action?: string
  reason?: string
}

/** POST /api/episodes/{id}/reparse 响应(= 后端 EpisodeReparseOut) */
export interface EpisodeReparseOut {
  episode_id: number
  dry_run: boolean
  parsed: EpisodeReparseParsed | null
  action: EpisodeReparseAction
}

/** GET /api/report 单日聚合点(= CLI report --json parse_events.days 条目) */
export interface ReportDayPoint {
  date: string
  events: number
  llm_called: number
  llm_call_rate: number
  by_level: Record<string, number>
  avg_latency_ms: number | null
}

/**
 * GET /api/report 响应(= CLI report --json 同构,单一事实源
 * autoanime.cli._aggregate_report):累计解析事件 + 审计 + 人工介入率。
 */
export interface ReportOut {
  generated_from: { parse_events: number; audit_log: number }
  parse_events: {
    total: number
    days: ReportDayPoint[]
    llm_called_total: number
    llm_call_rate: number
    by_outcome: Record<string, number>
  }
  audit: {
    total: number
    by_action: Record<string, number>
    by_actor: Record<string, number>
  }
  manual_intervention_rate: {
    manual_correction_events: number
    archived_events: number
    rate: number | null
    note: string
  }
}

// ---------- 目录浏览:GET /api/filesystem(P1-D) ----------

/** GET /api/filesystem 响应(= 后端 FilesystemListing;files 仅 include_files 时填充) */
export interface FilesystemListing {
  /** 当前目录(resolve 后);空串 = Windows 盘符根视图 */
  path: string
  /** 上一级目录;根目录(null)时「上一级」禁用 */
  parent: string | null
  directories: string[]
  /** 文件名(include_files=true 时填充;文件选择模式用) */
  files: string[]
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

// ---------- Mikan 字幕组发现:GET /api/mikan/subtitle_groups ----------

/** 一个字幕组的公开 RSS 订阅地址(可直接挂 RssSource,无 token) */
export interface MikanGroupOptionDto {
  group_id: string
  group_name: string
  rss_url: string
}

/** 发现结果:匹配到的 Mikan 番剧 + 可选字幕组列表 */
export interface MikanGroupsDto {
  matched_title: string
  bangumi_id: number
  groups: MikanGroupOptionDto[]
}

// ---------- 通用 RSS 匹配预览(POST /api/pipeline/rss-preview) ----------

export interface RssPreviewBody {
  rss_url: string
  rss_token?: string
  title_cn?: string
  title_jp?: string
  title_romaji?: string
  season_number?: number
  fansub_pref?: string | null
  include_keywords?: string | null
  exclude_keywords?: string | null
  limit?: number
}

/** verdict: would_download(将下载,每集评分最优唯一) / candidate / rejected / unparsed */
export interface RssPreviewEntryDto {
  title: string
  episode: number | null
  verdict: 'would_download' | 'candidate' | 'rejected' | 'unparsed'
  reason: string | null
  fansub: string | null
  score: number
}

export interface RssPreviewResponse {
  entries_total: number
  listed: number
  download_count: number
  entries: RssPreviewEntryDto[]
}
