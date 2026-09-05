/*
 * Pipeline 流量模型单测:路径映射(含优雅降级)、基线派生、token 推进、计数累加、边点亮。
 */
import {
  activeEdgesOf,
  flowReducer,
  initialFlowState,
  passingNodes,
  pathForEvent,
  pipelineBaseline,
  type FlowState,
} from '../pipelineFlow'
import type { Metrics, SseEvent } from '../../api/types'

function parseEvent(payload: Record<string, unknown>): SseEvent {
  return { id: '1', category: 'parse', message: '解析', payload, ts: '2026-09-06T10:00:00Z' }
}

function eventWith(category: SseEvent['category'], payload: Record<string, unknown> = {}): SseEvent {
  return { id: '1', category, message: category, payload, ts: '2026-09-06T10:00:00Z' }
}

describe('pathForEvent', () => {
  it('L1 高置信走快路径(跳过 L2/L3)', () => {
    expect(pathForEvent(parseEvent({ level: 1, outcome: 'l1_high' }))).toEqual([
      'input',
      'l1',
      'arbiter',
      'organize',
    ])
  })

  it('L2 记忆命中走记忆路径', () => {
    expect(pathForEvent(parseEvent({ level: 2, outcome: 'memory_hit' }))).toEqual([
      'input',
      'l1',
      'l2',
      'arbiter',
      'organize',
    ])
  })

  it('低置信度转入人工确认', () => {
    expect(pathForEvent(parseEvent({ level: 1, outcome: 'low_confidence' }))).toEqual([
      'input',
      'l1',
      'l2',
      'arbiter',
      'pending',
    ])
  })

  it('未识别级别走 L3 全链路', () => {
    expect(pathForEvent(parseEvent({ level: 3 }))).toEqual([
      'input',
      'l1',
      'l2',
      'l3',
      'arbiter',
      'organize',
    ])
  })

  it('优雅降级:parse 事件缺 level/outcome/confidence 时不画路径(只进最近列表)', () => {
    // 后端 web 层 parse 事件 payload 只有 pending_id/title/audit_id
    expect(pathForEvent(parseEvent({ pending_id: 7, title: '葬送的芙莉莲', audit_id: 42 }))).toBeNull()
    expect(pathForEvent(parseEvent({}))).toBeNull()
  })

  it('download/organize/system 事件路径', () => {
    expect(pathForEvent(eventWith('download'))).toEqual(['input', 'l1'])
    expect(pathForEvent(eventWith('organize'))).toEqual(['arbiter', 'organize'])
    expect(pathForEvent(eventWith('system'))).toBeNull()
    expect(pathForEvent(eventWith('error'))).toBeNull()
  })
})

describe('pipelineBaseline(由 /api/metrics.by_level 派生)', () => {
  it('按 level 1/2/3 取 total,总数为三级之和', () => {
    const metrics: Metrics = {
      intervention_rate: null,
      audit_total: 0,
      audit_manual: 0,
      by_level: [
        { level: 1, total: 291, llm_called: 0, outcomes: {} },
        { level: 2, total: 96, llm_called: 0, outcomes: {} },
        { level: 3, total: 44, llm_called: 31, outcomes: {} },
      ],
      llm_call_curve_weekly: [],
      pending_trend_daily: [],
      pending_open: 0,
      episode_states: {},
      memory_sources: [],
    }
    expect(pipelineBaseline(metrics)).toEqual({
      total: 431,
      l1_high: 291,
      l2_hit: 96,
      l3_entered: 44,
    })
  })

  it('缺失级别按 0 计', () => {
    const metrics: Metrics = {
      intervention_rate: null,
      audit_total: 0,
      audit_manual: 0,
      by_level: [{ level: 1, total: 10, llm_called: 0, outcomes: {} }],
      llm_call_curve_weekly: [],
      pending_trend_daily: [],
      pending_open: 0,
      episode_states: {},
      memory_sources: [],
    }
    expect(pipelineBaseline(metrics)).toEqual({
      total: 10,
      l1_high: 10,
      l2_hit: 0,
      l3_entered: 0,
    })
  })
})

describe('flowReducer', () => {
  const base: FlowState = {
    ...initialFlowState,
    counters: {
      input: 100,
      l1: 100,
      l2: 50,
      l3: 10,
      arbiter: 90,
      organize: 85,
      pending: 0,
    },
    entered: {
      input: 100,
      l1: 100,
      l2: 50,
      l3: 10,
      arbiter: 90,
      organize: 85,
      pending: 0,
    },
  }

  it('seed 用 metrics 派生基线建立计数', () => {
    const state = flowReducer(initialFlowState, {
      type: 'seed',
      baseline: { total: 431, l1_high: 291, l2_hit: 96, l3_entered: 44 },
    })
    expect(state.counters.l1).toBe(431)
    expect(state.counters.l2).toBe(431 - 291)
    expect(state.counters.l3).toBe(44)
    expect(state.counters.arbiter).toBe(291 + 96 + 44)
    expect(state.counters.organize).toBe(291 + 96)
    expect(state.entered.l1).toBe(431)
  })

  it('SSE parse 事件生成 token 并记入最近事件', () => {
    const state = flowReducer(base, { type: 'event', event: parseEvent({ level: 1 }) })
    expect(state.tokens).toHaveLength(1)
    expect(state.recent[0]?.message).toBe('解析')
  })

  it('降级 parse 事件只进最近列表,不生成 token', () => {
    const state = flowReducer(base, {
      type: 'event',
      event: parseEvent({ pending_id: 7, title: '药屋少女的呢喃' }),
    })
    expect(state.tokens).toHaveLength(0)
    expect(state.recent[0]?.message).toBe('解析')
  })

  it('token 沿路径推进,抵达终点后计数 +1 并移除', () => {
    let state = flowReducer(base, {
      type: 'event',
      event: parseEvent({ level: 1, outcome: 'l1_high' }), // path: input→l1→arbiter→organize
    })
    // step 0 → 1 → 2,第三次 tick 抵达 organize
    state = flowReducer(state, { type: 'tick' })
    expect(state.tokens[0]?.step).toBe(1)
    state = flowReducer(state, { type: 'tick' })
    expect(state.tokens[0]?.step).toBe(2)
    expect(activeEdgesOf(state.tokens)).toEqual(new Set(['e-arbiter-organize']))
    state = flowReducer(state, { type: 'tick' })
    expect(state.tokens).toHaveLength(0)
    expect(state.counters.organize).toBe(86)
  })

  it('低置信事件终点是 pending', () => {
    let state = flowReducer(base, {
      type: 'event',
      event: parseEvent({ outcome: 'low_confidence', confidence: 'low' }),
    })
    // path: input→l1→l2→arbiter→pending(5 节点 4 段,4 次 tick 完成)
    state = flowReducer(state, { type: 'tick' })
    state = flowReducer(state, { type: 'tick' })
    state = flowReducer(state, { type: 'tick' })
    expect(state.counters.pending).toBe(0)
    state = flowReducer(state, { type: 'tick' })
    expect(state.tokens).toHaveLength(0)
    expect(state.counters.pending).toBe(1)
  })

  it('clear 清空 token 与最近事件但保留计数', () => {
    let state = flowReducer(base, { type: 'event', event: eventWith('organize') })
    state = flowReducer(state, { type: 'clear' })
    expect(state.tokens).toHaveLength(0)
    expect(state.recent).toHaveLength(0)
    expect(state.counters.organize).toBe(85)
  })

  it('passingNodes 汇总 token 已抵达节点', () => {
    let state = flowReducer(base, {
      type: 'event',
      event: parseEvent({ level: 1, outcome: 'l1_high' }),
    })
    state = flowReducer(state, { type: 'tick' })
    const passing = passingNodes(state.tokens)
    expect(passing.has('input')).toBe(true)
    expect(passing.has('l1')).toBe(true)
    expect(passing.has('arbiter')).toBe(false)
  })
})
