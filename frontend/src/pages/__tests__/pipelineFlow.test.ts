/*
 * Pipeline 流量模型单测(mock SSE 事件驱动):路径映射、token 推进、计数累加、边点亮。
 */
import {
  activeEdgesOf,
  flowReducer,
  initialFlowState,
  passingNodes,
  pathForEvent,
  type FlowState,
} from '../pipelineFlow'
import type { SseEvent } from '../../api/types'

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

  it('download/organize/system 事件路径', () => {
    expect(pathForEvent(eventWith('download'))).toEqual(['input', 'l1'])
    expect(pathForEvent(eventWith('organize'))).toEqual(['arbiter', 'organize'])
    expect(pathForEvent(eventWith('system'))).toBeNull()
    expect(pathForEvent(eventWith('error'))).toBeNull()
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

  it('seed 用 metrics.levels 建立基线', () => {
    const state = flowReducer(initialFlowState, {
      type: 'seed',
      levels: { total: 431, l1_high: 291, l2_hit: 96, l3_entered: 44, llm_calls: 31 },
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
