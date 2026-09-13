/*
 * Mock API 处理器 —— 与真实端点客户端(src/api/endpoints.ts)同形状、同语义,
 * 并对齐 E2 后端行为(Page 信封/PendingResolveOut/rollback 404+409/订阅标题校验)。
 * VITE_USE_MOCK=0(或生产构建)即整体关闭,本模块零参与打包路径。
 * 有意做成内存态:增删改在会话内可见,便于演示与测试交互闭环。
 */
import type * as RealEndpoints from '../api/endpoints'
import { ApiError } from '../api/client'
import {
  mockAudit,
  mockFilesystemListing,
  mockMetrics,
  mockMikanGroups,
  mockRssPreview,
  mockPending,
  mockRssSources,
  mockSeasonBrowse,
  mockSeasonCalendar,
  mockSeries,
  mockSettings,
  mockSubscriptions,
} from './data'
import type {
  AuditDto,
  ConfirmNameOut,
  EpisodeReparseOut,
  Metrics,
  OperationGroupDto,
  Page,
  PendingItemDto,
  PendingResolveOut,
  ReportOut,
  RssPollResult,
  RssSourceDto,
  RollbackResult,
  SeriesDto,
  SettingsDto,
  SettingsUpdateBody,
  SettingEffect,
  SubscriptionCreateBody,
  SubscriptionDto,
  RssPreviewBody,
  SubscriptionUpdateBody,
} from '../api/types'

function clone<T>(value: T): T {
  return structuredClone(value)
}

interface MockState {
  series: SeriesDto[]
  pending: PendingItemDto[]
  audit: AuditDto[]
  subscriptions: SubscriptionDto[]
  rssSources: RssSourceDto[]
  settings: SettingsDto
  metrics: Metrics
  nextId: number
  nextOpSeq: number
}

/** mock settings 的初始 updated_at(并发写冲突基线;须在 freshState 首次调用前初始化) */
export const MOCK_SETTINGS_UPDATED_AT = '2026-09-08T08:00:00.000Z'

let state: MockState = freshState()

function freshState(): MockState {
  return {
    series: clone(mockSeries),
    pending: clone(mockPending),
    audit: clone(mockAudit),
    subscriptions: clone(mockSubscriptions),
    rssSources: clone(mockRssSources),
    settings: { ...clone(mockSettings), updated_at: MOCK_SETTINGS_UPDATED_AT },
    metrics: clone(mockMetrics),
    nextId: 1000,
    nextOpSeq: 1,
  }
}

/** 测试用:重置 mock 数据到初始 fixtures */
export function resetMockState(): void {
  state = freshState()
}

/** 测试用:覆写 metrics 基线(pending_open 仍由 pending 列表派生) */
export function setMockMetrics(metrics: Metrics): void {
  state.metrics = clone(metrics)
}

/** Page 信封:与后端 schemas.Page 一致(total/limit/offset/items) */
function paginate<T>(items: T[], limit?: number, offset?: number): Page<T> {
  const lim = limit ?? 50
  const start = offset ?? 0
  return {
    total: items.length,
    limit: lim,
    offset: start,
    items: items.slice(start, start + lim),
  }
}

function delayVoid(): Promise<void> {
  return new Promise((resolve) => setTimeout(() => resolve(undefined), 120))
}

function delayed<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), 120))
}

function nextOperationId(): string {
  const id = `op-mock-${String(state.nextOpSeq++).padStart(4, '0')}`
  return id
}

export function createMockApi(): (typeof RealEndpoints)['endpoints'] {
  return {
    metrics: {
      get: () => {
        const metrics: Metrics = {
          ...state.metrics,
          pending_open: state.pending.filter((p) => p.status === 'pending').length,
        }
        return delayed(metrics)
      },
    },

    series: {
      list: (query = {}) => {
        const needle = (query.q ?? '').toLowerCase()
        const filtered =
          needle === ''
            ? state.series
            : state.series.filter(
                (series) =>
                  (series.title_cn ?? '').toLowerCase().includes(needle) ||
                  (series.title_jp ?? '').toLowerCase().includes(needle) ||
                  (series.title_romaji ?? '').toLowerCase().includes(needle),
              )
        return delayed(paginate(filtered, query.limit, query.offset))
      },
      // 海报 URL 构造与真实端点一致(mock 不拦截 <img>,由 vite proxy/MSW 之外处理)
      posterUrl: (id: number) => `/api/series/${id}/poster`,
    },

    pending: {
      list: (query = {}) => {
        const status = query.status ?? 'pending'
        const filtered =
          status === 'pending' || status === 'resolved' || status === 'skipped'
            ? state.pending.filter((p) => p.status === status)
            : state.pending
        return delayed(paginate(filtered, query.limit, query.offset))
      },
      // 12-F:对齐后端 PendingConfirmIn | None——body 可选,提供时按非 None 字段覆写草稿
      confirm: (id, body?) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `pending ${id} not found`)
          })
        }
        if (item.status !== 'pending') {
          return delayVoid().then(() => {
            throw new ApiError(409, `pending ${id} already resolved (status=${item.status})`)
          })
        }
        if (body !== undefined) {
          if (body.title !== undefined) item.context.title = body.title
          if (body.season !== undefined) item.context.season = body.season
          if (body.episode !== undefined) item.context.episode = body.episode
          if (body.segment !== undefined) item.context.segment = body.segment
          if (body.fansub !== undefined) item.context.fansub = body.fansub
        }
        item.status = 'resolved'
        item.resolved_at = new Date().toISOString()
        item.resolved_by = 'manual'
        item.resolution = { action: 'confirm', confirmed_title: String(item.context.title ?? item.raw_name) }
        return delayed({
          id: item.id,
          status: item.status,
          resolution: item.resolution,
          resolved_by: 'manual',
          learned_entries: 2,
          bypassed: false,
        } satisfies PendingResolveOut)
      },
      correct: (id, body) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `pending ${id} not found`)
          })
        }
        // 对齐后端 PendingCorrectIn:title 必填
        if (body.title === undefined || body.title.trim() === '') {
          return delayVoid().then(() => {
            throw new ApiError(422, "correct requires a non-empty 'title'")
          })
        }
        // 对齐学习三件套语义:纠正即覆盖草稿字段 + 负记忆
        item.context.title = body.title
        if (body.season !== undefined) item.context.season = body.season
        if (body.episode !== undefined) item.context.episode = body.episode
        if (body.segment !== undefined) item.context.segment = body.segment
        if (body.fansub !== undefined) item.context.fansub = body.fansub
        item.status = 'resolved'
        item.resolved_at = new Date().toISOString()
        item.resolved_by = 'manual'
        item.reason = '人工纠正,已沉淀进解析记忆'
        item.resolution = { action: 'correct', confirmed_title: body.title }
        return delayed({
          id: item.id,
          status: item.status,
          resolution: item.resolution,
          resolved_by: 'manual',
          learned_entries: 2,
          bypassed: true,
        } satisfies PendingResolveOut)
      },
      // 12-F:对齐后端 PendingRejectIn | None——body 可选,reason 记入 resolution
      reject: (id, body?) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `pending ${id} not found`)
          })
        }
        if (item.status !== 'pending') {
          return delayVoid().then(() => {
            throw new ApiError(409, `pending ${id} already resolved (status=${item.status})`)
          })
        }
        item.status = 'skipped'
        item.resolved_at = new Date().toISOString()
        item.resolved_by = 'manual'
        item.resolution = { action: 'reject', confirmed_title: String(item.context.title ?? item.raw_name) }
        if (body?.reason !== undefined) {
          item.resolution = { ...item.resolution, reason: body.reason }
        }
        return delayed({
          id: item.id,
          status: item.status,
          resolution: item.resolution,
          resolved_by: 'manual',
          learned_entries: 0,
          bypassed: false,
        } satisfies PendingResolveOut)
      },
    },

    audit: {
      list: (query = {}) => {
        let items = state.audit
        if (query.operation_id) {
          items = items.filter((a) => a.operation_id === query.operation_id)
        }
        if (query.entity) {
          items = items.filter((a) => a.entity === query.entity)
        }
        if (query.action) {
          items = items.filter((a) => a.action === query.action)
        }
        const sorted = [...items].sort((a, b) => b.id - a.id)
        return delayed(paginate(sorted, query.limit, query.offset))
      },
    },

    auditOperations: {
      list: (query = {}) => {
        const groups = new Map<string, AuditDto[]>()
        for (const row of state.audit) {
          const list = groups.get(row.operation_id) ?? []
          list.push(row)
          groups.set(row.operation_id, list)
        }
        const items: OperationGroupDto[] = [...groups.entries()]
          .map(([operationId, rows]) => {
            const sorted = [...rows].sort((a, b) => a.id - b.id)
            return {
              operation_id: operationId,
              rows: sorted.length,
              entities: [...new Set(sorted.map((r) => r.entity))].sort(),
              actions: [...new Set(sorted.map((r) => r.action))].sort(),
              first_audit_id: sorted[0]!.id,
              last_audit_id: sorted[sorted.length - 1]!.id,
              rollbackable: Object.keys(sorted[sorted.length - 1]!.reverse).length > 0,
            }
          })
          .sort((a, b) => b.last_audit_id - a.last_audit_id)
        return delayed(paginate(items, query.limit, query.offset))
      },
    },

    organize: {
      rollback: (auditId) => {
        const row = state.audit.find((a) => a.id === auditId)
        if (!row) {
          return delayVoid().then(() => {
            throw new ApiError(404, `audit row ${auditId} not found`)
          })
        }
        if (Object.keys(row.reverse).length === 0) {
          return delayVoid().then(() => {
            throw new ApiError(
              409,
              `audit row ${auditId} carries no reverse instruction; nothing to roll back`,
            )
          })
        }
        const operationId = nextOperationId()
        // 对齐后端:撤销本身落一条新审计行(置顶下一组)
        state.audit.unshift({
          id: state.nextId++,
          operation_id: operationId,
          entity: row.entity,
          entity_id: row.entity_id,
          action: 'rollback',
          instruction: { rolled_back_audit_id: auditId, applied: {}, skipped: {} },
          reverse: { rollback_of: auditId },
          actor: 'manual',
        })
        const result: RollbackResult = {
          audit_id: auditId,
          operation_id: operationId,
          applied: { applied: {}, skipped: {} },
          learned: false,
        }
        return delayed(result)
      },
    },

    subscriptions: {
      list: (query = {}) => delayed(paginate(state.subscriptions, query.limit, query.offset)),
      create: (body: SubscriptionCreateBody) => {
        // 对齐后端 SubscriptionCreateIn:至少一个标题
        if (!body.title_cn && !body.title_jp && !body.title_romaji) {
          return delayVoid().then(() => {
            throw new ApiError(422, 'at least one of title_cn/title_jp/title_romaji is required')
          })
        }
        const episodeCount = body.episode_count ?? 0
        const seasonNumber = body.season_number ?? 1
        // P0-B 一步订阅:rss_url 提供时同事务挂源;rss_saved 只在创建响应有语义
        const rssSaved = Boolean(body.rss_url)
        const sub: SubscriptionDto = {
          id: state.nextId++,
          title_cn: body.title_cn ?? null,
          title_jp: body.title_jp ?? null,
          title_romaji: body.title_romaji ?? null,
          media_type: body.media_type ?? 'tv',
          include_keywords: body.include_keywords ?? null,
          exclude_keywords: body.exclude_keywords ?? null,
          status: 'active',
          fansub_pref: body.fansub_pref ?? null,
          quality_pref: body.quality_pref ?? null,
          seasons: [
            {
              season_id: state.nextId++,
              number: seasonNumber,
              status: 'upcoming',
              episodes_total: episodeCount,
              // 预生成集表 = 全部 MISSING
              episodes_missing: episodeCount,
              episodes_organized: 0,
              rss_sources: rssSaved ? 1 : 0,
            },
          ],
          rss_saved: rssSaved,
        }
        state.subscriptions.unshift(sub)
        if (rssSaved) {
          state.rssSources.unshift({
            id: state.nextId++,
            url: body.rss_url!,
            has_token: Boolean(body.rss_token),
            season_id: sub.seasons[0]!.season_id,
            enabled: true,
            last_polled_at: null,
          })
        }
        return delayed(clone(sub))
      },
      update: (id, body: SubscriptionUpdateBody) => {
        const sub = state.subscriptions.find((s) => s.id === id)
        if (!sub) {
          return delayVoid().then(() => {
            throw new ApiError(404, `subscription ${id} not found`)
          })
        }
        if (body.status !== undefined) sub.status = body.status
        if (body.fansub_pref !== undefined) sub.fansub_pref = body.fansub_pref
        if (body.quality_pref !== undefined) sub.quality_pref = body.quality_pref
        return delayed(clone(sub))
      },
      remove: (id) => {
        state.subscriptions = state.subscriptions.filter((s) => s.id !== id)
        return delayVoid()
      },
    },

    rssSources: {
      list: (query = {}) => delayed(paginate(state.rssSources, query.limit, query.offset)),
      create: (body) => {
        if (!body.url) {
          return delayVoid().then(() => {
            throw new ApiError(422, 'url must be a non-empty string')
          })
        }
        // 对齐后端 RssSourceCreateIn:season_id 必填(外键)
        if (body.season_id === undefined) {
          return delayVoid().then(() => {
            throw new ApiError(422, 'season_id is required')
          })
        }
        const source: RssSourceDto = {
          id: state.nextId++,
          url: body.url,
          has_token: Boolean(body.token),
          season_id: body.season_id,
          enabled: body.enabled ?? true,
          last_polled_at: null,
        }
        state.rssSources.unshift(source)
        return delayed(clone(source))
      },
      update: (id, body) => {
        const source = state.rssSources.find((s) => s.id === id)
        if (!source) {
          return delayVoid().then(() => {
            throw new ApiError(404, `rss source ${id} not found`)
          })
        }
        if (body.enabled !== undefined) source.enabled = body.enabled
        if (body.url !== undefined) source.url = body.url
        if (body.token !== undefined) source.has_token = body.token !== null
        return delayed(clone(source))
      },
      remove: (id) => {
        state.rssSources = state.rssSources.filter((s) => s.id !== id)
        return delayVoid()
      },
      // 12-F:行内立即轮询(对齐后端 404/409(disabled)语义,并刷新 last_polled_at)
      poll: (id) => {
        const source = state.rssSources.find((s) => s.id === id)
        if (!source) {
          return delayVoid().then(() => {
            throw new ApiError(404, `rss source ${id} not found`)
          })
        }
        if (!source.enabled) {
          return delayVoid().then(() => {
            throw new ApiError(409, `rss source ${id} is disabled`)
          })
        }
        source.last_polled_at = new Date().toISOString()
        const result: RssPollResult = {
          source_id: source.id,
          season_id: source.season_id,
          skipped_not_due: false,
          fetch_error: null,
          entries_total: 12,
          seen: 3,
          rejected: 8,
          backlog: 1,
          picked: 2,
          gaps: [],
          reconciled: 0,
          reconcile_notes: [],
          download: { checked: 4, completed: 1, failed: 0, retried: 0, notes: [] },
        }
        return delayed(result)
      },
    },

    // ---- P1-E:季度选番(与真实端点同构:degraded=true 时 items 空) ----
    seasonCalendar: {
      get: () => delayed(clone(mockSeasonCalendar)),
    },
    seasonBrowse: {
      get: (_query) => delayed(clone(mockSeasonBrowse)),
    },

    mikan: {
      // mock 不做真实标题匹配,固定返回两个字幕组选项
      subtitleGroups: (_title: string) => delayed(clone(mockMikanGroups)),
    },

    // P1-D 目录浏览(最小 stub:path 缺省 = 盘符根视图;includeFiles 附文件清单)
    filesystem: {
      list: (path?: string, includeFiles?: boolean) =>
        delayed(
          clone({
            ...mockFilesystemListing,
            ...(path === undefined ? {} : { path }),
            files: includeFiles
              ? ['[LoliHouse] 孤独摇滚 - 01 [1080p][简中].mkv', 'README.md']
              : [],
          }),
        ),
    },

    // 12-F:集重新识别(对齐后端两步契约:dry_run=true 预览 / false 执行)
    episodes: {
      reparse: (id, body) => {
        const episode = state.series
          .flatMap((s) => s.seasons)
          .flatMap((season) => season.episodes)
          .find((e) => e.id === id)
        if (!episode) {
          return delayVoid().then(() => {
            throw new ApiError(404, `episode ${id} not found`)
          })
        }
        if (episode.file_path === null) {
          return delayVoid().then(() => {
            throw new ApiError(409, `episode ${id} has no file on disk to reparse`)
          })
        }
        const padded = String(episode.number).padStart(2, '0')
        const result: EpisodeReparseOut = {
          episode_id: id,
          dry_run: body.dry_run,
          parsed: {
            title: '葬送的芙莉莲',
            season: 1,
            episode: episode.number,
            segment: 'episode',
            fansub: null,
            level: 'high',
            confidence: 1,
            missing_fields: [],
            evidence: {},
          },
          action: {
            dst: `/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E${padded}.1080p.mkv`,
            strategy: 'hardlink',
            episode_state: episode.state,
            action: 'archive',
          },
        }
        return delayed(result)
      },
    },

    pipeline: {
      parsePreview: (_body) =>
        delayed({
          route: 'archive',
          result: {
            title: _body.name,
            season: 1,
            episode: 1,
            segment: 'episode',
            fansub: null,
            level: 'high',
            confidence: 1,
            missing_fields: [],
            evidence: {},
          },
        }),
      startImport: (_body) => {
        const id = `task-${state.nextId++}`
        return delayed({ task_id: id, status: 'running' as const })
      },
      task: (id) =>
        delayed({
          task_id: id,
          kind: 'import' as const,
          status: 'completed' as const,
          directory: '',
          dry_run: true,
          created_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
          processed: 0,
          total: 0,
          summary: { total: 0, scanned: 0, archived: 0, pending: 0, failed: 0, skipped: 0 },
          error: null,
        }),
      // 12-F:库外人工确认命名(对齐后端 ConfirmNameIn.name 必填 → 422 语义)
      confirmName: (body) => {
        if (body.name.trim() === '') {
          return delayVoid().then(() => {
            throw new ApiError(422, 'name must be a non-empty string')
          })
        }
        const result: ConfirmNameOut = {
          bypassed: false,
          resolved_pending: 0,
          archive: {
            archived: true,
            dst: '/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E01.1080p.mkv',
            strategy: 'hardlink',
          },
          entries: [
            {
              key_level: 'show',
              key_hash: 'mock-hash-show',
              title_shape: '葬送的芙莉莲',
              source: 'manual',
              status: 'active',
              hit_count: 0,
              corrected_count: 0,
            },
            {
              key_level: 'episode',
              key_hash: 'mock-hash-episode',
              title_shape: '葬送的芙莉莲',
              source: 'manual',
              status: 'active',
              hit_count: 0,
              corrected_count: 0,
            },
          ],
        }
        return delayed(result)
      },
      rssPreview: (_body: RssPreviewBody) => delayed(clone(mockRssPreview)),
    },

    scheduler: {
      // 12-F:对齐后端 SchedulerRunIn——body 可选,scope 缺省 all
      runOnce: (body = {}) => delayed({ scope: body.scope ?? ('all' as const), reports: {}, errors: [] }),
    },

    // 12-F:识别指标(GET /api/report,与 CLI report --json 同构;纯读 fixture)
    report: {
      get: () =>
        delayed({
          generated_from: { parse_events: 431, audit_log: 12 },
          parse_events: {
            total: 431,
            days: [],
            llm_called_total: 31,
            llm_call_rate: 0.0719,
            by_outcome: { archive: 387, low_confidence: 44 },
          },
          audit: {
            total: 12,
            by_action: { pending_confirm: 8, pending_correct: 2, organize: 2 },
            by_actor: { auto: 10, manual: 2 },
          },
          manual_intervention_rate: {
            manual_correction_events: 2,
            archived_events: 387,
            rate: 0.0052,
            note: 'mock fixture',
          },
        } satisfies ReportOut),
    },

    // ---- 12-E:settings 三档生效 mock(对齐后端 settings.py 白名单/密钥语义) ----
    settings: {
      get: () => delayed(clone(state.settings)),
      update: (body: SettingsUpdateBody) => {
        // 并发写冲突:base_updated_at 与服务端当前 updated_at 不一致 → 409(固定 detail,对齐后端契约)
        if (
          body.base_updated_at !== undefined &&
          body.base_updated_at !== (state.settings.updated_at ?? null)
        ) {
          return delayed(clone(state.settings)).then(() => {
            throw new ApiError(409, 'settings_changed')
          })
        }
        // 三档白名单(与后端 settings.py 的 _IMMEDIATE/_SCHEDULER/_RESTART 集合一致)
        const immediate = new Set([
          'dry_run',
          'l2_enabled',
          'llm_enabled',
          'llm_model',
          'reference_enabled',
          'reference_order',
          'llm_timeout_s',
          'llm_max_retries',
          'reference_qps',
          'pending_backlog_alert_threshold',
          'log_level',
        ])
        const scheduler = new Set([
          'scheduler_enabled',
          'rss_poll_interval_minutes',
          'rss_poll_jitter_pct',
          'download_poll_interval_s',
          'download_max_retries',
          'collected_check_days',
        ])
        // 密钥字段 → GET has_* 字段名(值永不回显)
        const secretHasField: Record<string, keyof SettingsDto> = {
          llm_api_key: 'has_llm_api_key',
          tmdb_api_key: 'has_tmdb_api_key',
          qbittorrent_password: 'has_qbittorrent_password',
          notify_webhook_url: 'has_notify_webhook_url',
          notify_telegram_bot_token: 'has_notify_telegram_bot_token',
        }
        const effectFor = (key: string): SettingEffect =>
          scheduler.has(key) ? 'scheduler_rebuild' : immediate.has(key) ? 'immediate' : 'requires_restart'
        const applied: Record<string, SettingEffect> = {}
        for (const [key, value] of Object.entries(body)) {
          if (key === 'base_updated_at') continue // 并发基线,非配置字段
          if (key in secretHasField) {
            if (value === null) {
              // 显式 null = 清除(删 DB 覆盖回落 env/toml)
              ;(state.settings[secretHasField[key]!] as boolean) = false
              applied[key] = effectFor(key)
              continue
            }
            if (value === '') continue // 空串 = 不修改
            ;(state.settings[secretHasField[key]!] as boolean) = true
            applied[key] = effectFor(key)
            continue
          }
          if (value === null) continue // 非密钥字段 null = 不修改(v1 行为)
          ;(state.settings as unknown as Record<string, unknown>)[key] = value
          applied[key] = effectFor(key)
        }
        // 保存成功 → 服务端 updated_at 前移(下次 PUT 的冲突基线随之变化)
        state.settings.updated_at = new Date().toISOString()
        return delayed({ ...clone(state.settings), applied, warnings: [] })
      },
      // 12-E:notify-test —— 按 has_* 构造通道结果(未配置通道跳过)
      notifyTest: () => {
        const results: { channel: string; ok: boolean; error: string | null }[] = []
        if (state.settings.has_notify_webhook_url) {
          results.push({ channel: 'webhook', ok: true, error: null })
        }
        if (state.settings.has_notify_telegram_bot_token && state.settings.notify_telegram_chat_id) {
          results.push({ channel: 'telegram', ok: true, error: null })
        }
        return delayed({ results })
      },
      qbitTest: () => delayed({ ok: true, version: 'v2.0.9', save_path: 'C:/downloads', error: null }),
    },
  }
}
