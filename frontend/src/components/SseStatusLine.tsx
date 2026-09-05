/*
 * 全局 SSE 连接状态(小色标 + 文案),侧栏底部/移动顶栏共用。
 */
import { useEventStream } from '../hooks/eventStreamContext'
import { strings } from '../strings'
import { StatusDot, StatusMark, type Tone } from './StatusDot'

export function SseStatusLine({ compact = false }: { compact?: boolean }) {
  const { status, attempt } = useEventStream()
  let tone: Tone = 'neutral'
  let label: string = strings.sse.disconnected
  if (status === 'open') {
    tone = 'success'
    label = strings.sse.connected
  } else if (status === 'connecting' || status === 'reconnecting') {
    tone = 'warning'
    label = status === 'reconnecting' ? `${strings.sse.reconnecting} ×${attempt}` : strings.sse.connecting
  }
  if (compact) {
    return (
      <span className="inline-flex items-center">
        <StatusMark tone={tone} size={7} />
        <span className="sr-only">{label}</span>
      </span>
    )
  }
  return <StatusDot tone={tone} size={7} label={label} className="text-xs" />
}
