/*
 * 展示层映射:领域状态 → 中文文案 + 小色标 tone(颜色不上正文,只上色标)。
 */
import type { Tone } from '../components/StatusDot'
import { strings } from '../strings'
import type {
  EpisodeState,
  MediaType,
  SeasonState,
} from '../api/types'

export interface StateView {
  label: string
  tone: Tone
}

export function episodeStateView(state: EpisodeState): StateView {
  switch (state) {
    case 'organized':
      return { label: strings.library.state.organized, tone: 'success' }
    case 'upgraded':
      return { label: strings.library.state.upgraded, tone: 'primary' }
    case 'downloading':
      return { label: strings.library.state.downloading, tone: 'info' }
    case 'downloaded':
      return { label: strings.library.state.downloaded, tone: 'warning' }
    case 'missing':
      return { label: strings.library.state.missing, tone: 'danger' }
    case 'ignored':
      return { label: strings.library.state.ignored, tone: 'neutral' }
  }
}

export function seasonStateView(state: SeasonState): StateView {
  switch (state) {
    case 'airing':
      return { label: strings.library.seasonState.airing, tone: 'info' }
    case 'ended':
      return { label: strings.library.seasonState.ended, tone: 'neutral' }
    case 'collected':
      return { label: strings.library.seasonState.collected, tone: 'success' }
    case 'upcoming':
      return { label: strings.library.seasonState.upcoming, tone: 'neutral' }
  }
}

export function mediaTypeLabel(type: MediaType): string {
  switch (type) {
    case 'tv':
      return strings.library.mediaType.tv
    case 'movie':
      return strings.library.mediaType.movie
    case 'ova':
      return strings.library.mediaType.ova
    case 'special':
      return strings.library.mediaType.special
  }
}

/** quality_score 小色标 tone:分数越高越接近满档 */
export function qualityTone(score: number | null): Tone {
  if (score === null) return 'neutral'
  if (score >= 10) return 'success'
  if (score >= 8) return 'primary'
  return 'warning'
}

// ---------- 数值/时间格式化(数据文本一律 mono + tabular-nums) ----------

export function formatPercent(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`
}

/** ISO datetime → 本地时区 'MM-DD HH:mm' */
export function formatDateTime(iso: string | null): string {
  if (!iso) return strings.common.never
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** ISO date(YYYY-MM-DD)→ 原样展示(D20:air_date JST 判定,展示转本地由后端给本地日期) */
export function formatDate(iso: string | null): string {
  return iso ?? '—'
}
