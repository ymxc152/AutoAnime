import { describe, expect, it } from 'vitest'
import { parseEventStream } from './events'

describe('parseEventStream', () => {
  it('returns ordered typed job events from SSE text', () => {
    const events = parseEventStream([
      'id: 1',
      'event: phase',
      'data: {"sequence":1,"message":"开始扫描","payload":{"name":"scan"}}',
      '',
      'id: 2',
      'event: scan_completed',
      'data: {"sequence":2,"message":"扫描完成","payload":{"plan_id":7}}',
      '',
    ].join('\n'))

    expect(events).toEqual([
      { id: 1, type: 'phase', sequence: 1, message: '开始扫描', payload: { name: 'scan' } },
      { id: 2, type: 'scan_completed', sequence: 2, message: '扫描完成', payload: { plan_id: 7 } },
    ])
  })
})
