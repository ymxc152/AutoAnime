/*
 * Mock API 处理器 —— 与真实端点客户端(src/api/endpoints.ts)同形状、同语义。
 * E2 合并后:VITE_USE_MOCK=0(或生产构建)即整体关闭,本模块零参与打包路径。
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
  PendingItemDto,
  RssSourceDto,
  SeriesDto,
  SettingsDto,
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
  }
}

/** 测试用:重置 mock 数据到初始 fixtures */
export function resetMockState(): void {
  state = freshState()
}

function paginate<T>(items: T[], limit?: number, offset?: number): { items: T[]; total: number } {
  const start = offset ?? 0
  const end = limit !== undefined ? start + limit : undefined
  return { items: items.slice(start, end), total: items.length }
}

function matchTitle(series: SeriesDto, q: string): boolean {
  const needle = q.toLowerCase()
  return (
    (series.title_cn ?? '').toLowerCase().includes(needle) ||
    (series.title_jp ?? '').toLowerCase().includes(needle) ||
    (series.title_romaji ?? '').toLowerCase().includes(needle)
  )
}

export function createMockApi(delayMs = 120): (typeof RealEndpoints)['endpoints'] {
  const delay = <T>(value: T): Promise<T> =>
    new Promise((resolve) => setTimeout(() => resolve(value), delayMs))

  const delayVoid = (): Promise<void> =>
    new Promise((resolve) => setTimeout(() => resolve(undefined), delayMs))

  return {
    metrics: {
      get: () => {
        const metrics: Metrics = {
          ...clone(mockMetrics),
          pending_count: state.pending.filter((p) => p.status === 'pending').length,
        }
        return delay(metrics)
      },
    },

    series: {
      list: (query = {}) => {
        const q = query.q ?? ''
        const filtered = q ? state.series.filter((s) => matchTitle(s, q)) : state.series
        return delay(paginate(filtered, query.limit, query.offset))
      },
    },

    pending: {
      list: (query = {}) => {
        const status = query.status ?? 'pending'
        const filtered =
          status === 'pending' || status === 'resolved' || status === 'skipped'
            ? state.pending.filter((p) => p.status === status)
            : state.pending
        return delay(paginate(filtered, query.limit, query.offset))
      },
      confirm: (id) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `待确认项 ${id} 不存在`)
          })
        }
        item.status = 'resolved'
        item.resolved_at = new Date().toISOString()
        return delay(clone(item))
      },
      correct: (id, body) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `待确认项 ${id} 不存在`)
          })
        }
        // 契约假设:纠正触发学习三件套(parse_memory + alias + bypass),此处仅模拟结果
        if (body.title !== undefined) item.parsed.title = { value: body.title, source: 'memory', confidence: 'high' }
        if (body.season !== undefined) item.parsed.season = { value: body.season, source: 'memory', confidence: 'high' }
        if (body.episode !== undefined) item.parsed.episode = { value: body.episode, source: 'memory', confidence: 'high' }
        if (body.fansub !== undefined) item.parsed.fansub = { value: body.fansub, source: 'memory', confidence: 'high' }
        if (body.resolution !== undefined) item.parsed.resolution = { value: body.resolution, source: 'memory', confidence: 'high' }
        item.status = 'resolved'
        item.resolved_at = new Date().toISOString()
        item.reason = '人工纠正,已沉淀进解析记忆'
        return delay(clone(item))
      },
      reject: (id) => {
        const item = state.pending.find((p) => p.id === id)
        if (!item) {
          return delayVoid().then(() => {
            throw new ApiError(404, `待确认项 ${id} 不存在`)
          })
        }
        item.status = 'skipped'
        item.resolved_at = new Date().toISOString()
        return delay(clone(item))
      },
    },

    audit: {
      list: (query = {}) => {
        let items = state.audit
        if (query.operation_id) {
          items = items.filter((a) => a.operation_id === query.operation_id)
        }
        const sorted = [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
        return delay(paginate(sorted, query.limit, query.offset))
      },
    },

    organize: {
      rollback: (operationId) => {
        const op = state.audit.find(
          (a) => a.operation_id === operationId && Object.keys(a.reverse).length > 0,
        )
        if (!op) {
          return delayVoid().then(() => {
            throw new ApiError(404, `操作 ${operationId} 不存在或没有可撤销的 reverse 指令`)
          })
        }
        return delay({ ok: true, operation_id: operationId })
      },
    },

    subscriptions: {
      list: (query = {}) => delay(paginate(state.subscriptions, query.limit, query.offset)),
      create: (body) => {
        if (!body.rss_url) {
          return delayVoid().then(() => {
            throw new ApiError(422, 'RSS 地址不能为空')
          })
        }
        const id = state.nextId++
        const series = state.series[0]
        const sub: SubscriptionDto = {
          id,
          series_id: series?.id ?? 0,
          title: series?.title_cn ?? '新订阅',
          season_id: series?.seasons[0]?.id ?? 0,
          season_number: series?.seasons[0]?.number ?? 1,
          state: 'airing',
          fansub_pref: body.fansub ?? null,
          episodes_total: 12,
          episodes_aired: 0,
          episodes_collected: 0,
          next_air_date: null,
          reduced_frequency: false,
          enabled: true,
        }
        state.subscriptions.unshift(sub)
        return delay(clone(sub))
      },
      remove: (id) => {
        state.subscriptions = state.subscriptions.filter((s) => s.id !== id)
        return delayVoid()
      },
    },

    rssSources: {
      list: (query = {}) => delay(paginate(state.rssSources, query.limit, query.offset)),
      create: (body) => {
        if (!body.url) {
          return delayVoid().then(() => {
            throw new ApiError(422, '源地址不能为空')
          })
        }
        const source: RssSourceDto = {
          id: state.nextId++,
          url: body.url,
          has_token: Boolean(body.token),
          season_id: body.season_id ?? null,
          enabled: true,
          last_polled_at: null,
        }
        state.rssSources.unshift(source)
        return delay(clone(source))
      },
      update: (id, body) => {
        const source = state.rssSources.find((s) => s.id === id)
        if (!source) {
          return delayVoid().then(() => {
            throw new ApiError(404, `RSS 源 ${id} 不存在`)
          })
        }
        if (body.enabled !== undefined) source.enabled = body.enabled
        if (body.url !== undefined) source.url = body.url
        return delay(clone(source))
      },
      remove: (id) => {
        state.rssSources = state.rssSources.filter((s) => s.id !== id)
        return delayVoid()
      },
    },

    settings: {
      get: () => delay(clone(state.settings)),
      update: (body) => {
        state.settings = clone(body)
        return delay(clone(state.settings))
      },
    },
  }
}
