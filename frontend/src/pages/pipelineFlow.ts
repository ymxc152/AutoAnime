/*
 * Pipeline 流量模型(纯 reducer,与 UI 解耦,可单测):
 * SSE 事件 → 路径 → token 沿节点推进 → 命中计数累加。
 */
import type { Metrics, SseEvent } from '../api/types'
import { strings } from '../strings'

export type PipelineNodeId = 'input' | 'l1' | 'l2' | 'l3' | 'arbiter' | 'organize' | 'pending'

export interface Token {
  id: string
  path: PipelineNodeId[]
  /** 当前所在段:已抵达 path[step],正向 path[step+1] 移动 */
  step: number
}

export interface RecentEvent {
  key: string
  message: string
  ts: string
  category: SseEvent['category']
}

/** Pipeline 节点展示数据(xyflow 自定义节点 data) */
export interface PipelineNodeData extends Record<string, unknown> {
  title: string
  desc: string
  passed: number
  entered: number
  passing: number
}

/** 管线基线:由 /api/metrics.by_level 派生(对齐后端 MetricsOut) */
export interface FlowBaseline {
  total: number
  l1_high: number
  l2_hit: number
  l3_entered: number
}

export interface FlowState {
  tokens: Token[]
  counters: Record<PipelineNodeId, number>
  /** 各节点累计进入数(命中率分母基线) */
  entered: Record<PipelineNodeId, number>
  recent: RecentEvent[]
  tokenSeq: number
}

const zeroCounters = (): Record<PipelineNodeId, number> => ({
  input: 0,
  l1: 0,
  l2: 0,
  l3: 0,
  arbiter: 0,
  organize: 0,
  pending: 0,
})

export const initialFlowState: FlowState = {
  tokens: [],
  counters: zeroCounters(),
  entered: zeroCounters(),
  recent: [],
  tokenSeq: 0,
}

/** /api/metrics → 管线基线:level N 的 total = 在该级得出结论的解析数 */
export function pipelineBaseline(metrics: Metrics): FlowBaseline {
  const byLevel = new Map(metrics.by_level.map((item) => [item.level, item]))
  const l1 = byLevel.get(1)?.total ?? 0
  const l2 = byLevel.get(2)?.total ?? 0
  const l3 = byLevel.get(3)?.total ?? 0
  return { total: l1 + l2 + l3, l1_high: l1, l2_hit: l2, l3_entered: l3 }
}

/**
 * 事件 payload → 管线路径。
 * 后端 web/sse 透传的 parse 事件不保证携带 level/outcome/confidence
 * (web 层自产的 pending 事件 payload 只有 pending_id/title/audit_id,
 * 回放通道是审计行信封):信息缺失时无法判定路径 → 返回 null,事件仍进
 * 最近事件列表,但不做 token 动画——不编造路径。
 */
export function pathForEvent(event: SseEvent): PipelineNodeId[] | null {
  if (event.category === 'parse') {
    const hasLevel = event.payload.level !== undefined && event.payload.level !== null
    const outcome = typeof event.payload.outcome === 'string' ? event.payload.outcome : ''
    const confidence = event.payload.confidence
    // 优雅降级:三类判定字段全缺 → 不走动画
    if (!hasLevel && outcome === '' && confidence === undefined) {
      return null
    }
    // 低置信判定必须先于 level 判定:低置信事件可能带任意 level
    if (outcome === 'low_confidence' || confidence === 'low') {
      return ['input', 'l1', 'l2', 'arbiter', 'pending']
    }
    const level = Number(event.payload.level ?? 0)
    if (level === 1 || outcome === 'l1_high') {
      return ['input', 'l1', 'arbiter', 'organize']
    }
    if (level === 2 || outcome === 'memory_hit') {
      return ['input', 'l1', 'l2', 'arbiter', 'organize']
    }
    return ['input', 'l1', 'l2', 'l3', 'arbiter', 'organize']
  }
  if (event.category === 'download') {
    return ['input', 'l1']
  }
  if (event.category === 'organize') {
    return ['arbiter', 'organize']
  }
  return null
}

export type FlowAction =
  | { type: 'seed'; baseline: FlowBaseline }
  | { type: 'event'; event: SseEvent }
  | { type: 'tick' }
  | { type: 'clear' }

function baseCounts(baseline: FlowBaseline): {
  counters: Record<PipelineNodeId, number>
  entered: Record<PipelineNodeId, number>
} {
  const counters: Record<PipelineNodeId, number> = {
    input: baseline.total,
    l1: baseline.total,
    l2: baseline.total - baseline.l1_high,
    l3: baseline.l3_entered,
    arbiter: baseline.l1_high + baseline.l2_hit + baseline.l3_entered,
    organize: baseline.l1_high + baseline.l2_hit,
    pending: 0,
  }
  return { counters, entered: { ...counters } }
}

export function flowReducer(state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case 'seed': {
      return { ...state, ...baseCounts(action.baseline) }
    }
    case 'event': {
      const path = pathForEvent(action.event)
      const recent = [
        {
          key: `${action.event.id ?? state.tokenSeq}-${action.event.ts}`,
          message: action.event.message,
          ts: action.event.ts,
          category: action.event.category,
        },
        ...state.recent,
      ].slice(0, 8)
      if (path === null) {
        return { ...state, recent }
      }
      const tokenSeq = state.tokenSeq + 1
      return {
        ...state,
        tokenSeq,
        recent,
        tokens: [...state.tokens, { id: `token-${tokenSeq}`, path, step: 0 }],
      }
    }
    case 'tick': {
      if (state.tokens.length === 0) return state
      const remaining: Token[] = []
      const counters = { ...state.counters }
      for (const token of state.tokens) {
        const nextStep = token.step + 1
        if (nextStep >= token.path.length - 1) {
          const dest = token.path[token.path.length - 1]!
          counters[dest] = (counters[dest] ?? 0) + 1
        } else {
          remaining.push({ ...token, step: nextStep })
        }
      }
      return { ...state, tokens: remaining, counters }
    }
    case 'clear':
      return { ...initialFlowState, counters: state.counters, entered: state.entered }
  }
}

/** token 当前已抵达的节点集合(用于节点"经过中"高亮) */
export function passingNodes(tokens: Token[]): Set<PipelineNodeId> {
  const set = new Set<PipelineNodeId>()
  for (const token of tokens) {
    for (const node of token.path.slice(0, token.step + 1)) {
      set.add(node)
    }
  }
  return set
}

/** token 正在穿越的边集合(用于点亮边) */
export function activeEdgesOf(tokens: Token[]): Set<string> {
  const active = new Set<string>()
  for (const token of tokens) {
    const source = token.path[token.step]
    const target = token.path[token.step + 1]
    if (source !== undefined && target !== undefined) {
      active.add(`e-${source}-${target}`)
    }
  }
  return active
}

// ---------- 节点/边静态定义 ----------

export const NODE_META: Record<
  PipelineNodeId,
  { title: string; desc: string; x: number; y: number }
> = {
  input: { title: strings.pipeline.node.input, desc: strings.pipeline.nodeDesc.input, x: 0, y: 130 },
  l1: { title: strings.pipeline.node.l1, desc: strings.pipeline.nodeDesc.l1, x: 210, y: 130 },
  l2: { title: strings.pipeline.node.l2, desc: strings.pipeline.nodeDesc.l2, x: 420, y: 130 },
  l3: { title: strings.pipeline.node.l3, desc: strings.pipeline.nodeDesc.l3, x: 420, y: 0 },
  arbiter: { title: strings.pipeline.node.arbiter, desc: strings.pipeline.nodeDesc.arbiter, x: 640, y: 130 },
  organize: { title: strings.pipeline.node.organize, desc: strings.pipeline.nodeDesc.organize, x: 860, y: 40 },
  pending: { title: strings.pipeline.node.pending, desc: strings.pipeline.nodeDesc.pending, x: 860, y: 220 },
}

export const EDGE_DEFS: Array<{ id: string; source: PipelineNodeId; target: PipelineNodeId }> = [
  { id: 'e-input-l1', source: 'input', target: 'l1' },
  { id: 'e-l1-arbiter', source: 'l1', target: 'arbiter' },
  { id: 'e-l1-l2', source: 'l1', target: 'l2' },
  { id: 'e-l2-l3', source: 'l2', target: 'l3' },
  { id: 'e-l2-arbiter', source: 'l2', target: 'arbiter' },
  { id: 'e-l3-arbiter', source: 'l3', target: 'arbiter' },
  { id: 'e-arbiter-organize', source: 'arbiter', target: 'organize' },
  { id: 'e-arbiter-pending', source: 'arbiter', target: 'pending' },
]
