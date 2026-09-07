/*
 * views 枚举兜底回归:后端新增枚举值时,展示层安全降级(label 原样、
 * tone neutral),不允许 TypeError 白屏。
 */
import { describe, expect, it } from 'vitest'
import {
  episodeStateLabel,
  episodeStateView,
  mediaTypeLabel,
  seasonStateView,
  subscriptionStatusLabel,
} from '../views'

describe('views 枚举兜底(未知枚举值不抛错)', () => {
  it('episodeStateView:未知枚举 label 原样、tone neutral', () => {
    const view = episodeStateView('future_state' as never)
    expect(view.label).toBe('future_state')
    expect(view.tone).toBe('neutral')
  })

  it('seasonStateView:未知枚举 label 原样、tone neutral', () => {
    const view = seasonStateView('hiatus' as never)
    expect(view.label).toBe('hiatus')
    expect(view.tone).toBe('neutral')
  })

  it('mediaTypeLabel:未知枚举原样返回', () => {
    expect(mediaTypeLabel('short' as never)).toBe('short')
  })

  it('subscriptionStatusLabel:未知枚举原样返回(既有兜底不回归)', () => {
    expect(subscriptionStatusLabel('archived')).toBe('archived')
  })

  it('episodeStateLabel:未知枚举原样返回(既有兜底不回归)', () => {
    expect(episodeStateLabel('verifying')).toBe('verifying')
  })

  it('已知枚举映射不受影响', () => {
    expect(episodeStateView('organized').label).toBe('已归档')
    expect(seasonStateView('airing').tone).toBe('info')
    expect(mediaTypeLabel('tv')).toBe('TV')
  })
})
