/*
 * Pipeline 流量模型(纯 reducer,与 UI 解耦,可单测):
 * SSE 事件 → 路径 → token 沿节点推进 → 命中计数累加。
 */
import type { LevelHits, SseEvent } from '../api/types'
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

/** 事件 payload → 管线路径(契约假设:parse 事件带 level/outcome) */
export function pathForEvent(event: SseEvent): PipelineNodeId[] | null {
  if (event.category === 'parse') {
    const level = Number(event.payload.level ?? 0)
    const outcome = String(event.payload.outcome ?? '')
    if (level === 1 || outcome === 'l1_high') {
      return ['input', 'l1', 'arbiter', 'organize']
    }
    if (level === 2 || outcome === 'memory_hit') {
      return ['input', 'l1', 'l2', 'arbiter', 'organize']
    }
    if (outcome === 'low_confidence' || event.payload.confidence === 'low') {
      return ['input', 'l1', 'l2', 'arbiter', 'pending']
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
  | { type: 'seed'; levels: LevelHits }
  | { type: 'event'; event: SseEvent }
  | { type: 'tick' }
  | { type: 'clear' }

function baseCounts(levels: LevelHits): {
  counters: Record<PipelineNodeId, number>
  entered: Record<PipelineNodeId, number>
} {
  const l2Enter = levels.total - levels.l1_high
  const arbiterEnter = levels.l1_high + levels.l2_hit + levels.l3_entered
  const counters: Record<PipelineNodeId, number> = {
    input: levels.total,
    l1: levels.total,
    l2: l2Enter,
    l3: levels.l3_entered,
    arbiter: arbiterEnter,
    organize: levels.l1_high + levels.l2_hit,
    pending: 0,
  }
  return { counters, entered: { ...counters } }
}

export function flowReducer(state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case 'seed': {
      return { ...state, ...baseCounts(action.levels) }
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
