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
  mockMetrics,
  mockPending,
  mockRssSources,
  mockSeries,
  mockSettings,
  mockSubscriptions,
} from './data'
import type {
  AuditDto,
  Metrics,
  OperationGroupDto,
  Page,
  PendingItemDto,
  PendingResolveOut,
  RssSourceDto,
  RollbackResult,
  SeriesDto,
  SettingsDto,
  SettingsUpdateBody,
  SubscriptionCreateBody,
  SubscriptionDto,
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
  nextId: number
  nextOpSeq: number
}

let state: MockState = freshState()

function freshState(): MockState {
  return {
    series: clone(mockSeries),
    pending: clone(mockPending),
    audit: clone(mockAudit),
    subscriptions: clone(mockSubscriptions),
    rssSources: clone(mockRssSources),
    settings: clone(mockSettings),
    nextId: 1000,
    nextOpSeq: 1,
  }
}

/** 测试用:重置 mock 数据到初始 fixtures */
export function resetMockState(): void {
  state = freshState()
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
          ...clone(mockMetrics),
          pending_open: state.pending.filter((p) => p.status === 'pending').length,
        }
        return delayed(metrics)
      },
    },

    series: {
      list: (query = {}) => delayed(paginate(state.series, query.limit, query.offset)),
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
      confirm: (id) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `pending ${id} not found`)
          })
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
      reject: (id) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `pending ${id} not found`)
          })
        }
        item.status = 'skipped'
        item.resolved_at = new Date().toISOString()
        item.resolved_by = 'manual'
        item.resolution = { action: 'reject', confirmed_title: String(item.context.title ?? item.raw_name) }
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
        const sub: SubscriptionDto = {
          id: state.nextId++,
          title_cn: body.title_cn ?? null,
          title_jp: body.title_jp ?? null,
          title_romaji: body.title_romaji ?? null,
          media_type: body.media_type ?? 'tv',
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
              rss_sources: 0,
            },
          ],
        }
        state.subscriptions.unshift(sub)
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
    },

    settings: {
      get: () => delayed(clone(state.settings)),
      update: (body: SettingsUpdateBody) => {
        // 对齐后端白名单覆写(进程内):dry_run/l2/llm/reference
        if (body.dry_run !== undefined) state.settings.dry_run = body.dry_run
        if (body.l2_enabled !== undefined) state.settings.l2_enabled = body.l2_enabled
        if (body.llm_enabled !== undefined) state.settings.llm_enabled = body.llm_enabled
        if (body.llm_model !== undefined) state.settings.llm_model = body.llm_model
        if (body.reference_enabled !== undefined) state.settings.reference_enabled = body.reference_enabled
        if (body.reference_order !== undefined) state.settings.reference_order = [...body.reference_order]
        return delayed(clone(state.settings))
      },
    },
  }
}
