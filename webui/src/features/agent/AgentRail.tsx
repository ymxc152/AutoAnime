import { JobEvent } from '../../api/events'

type MemoryRow = { alias_key: string; canonical_title: string; source: string }

export function AgentRail({
  events = [],
  memoryCount = 0,
  recentMemory = [],
}: {
  events?: JobEvent[]
  memoryCount?: number
  recentMemory?: MemoryRow[]
}) {
  const started = [...events].reverse().find(event => event.type === 'identify_started')
  const units = events.filter(event => event.type === 'identify_unit')
  const latest = units[units.length - 1]
  const totalUnits = Number(started?.payload?.units || 0)
  const skill = latest
    ? `正在识别：${String(latest.payload.title || latest.payload.hint_title || latest.payload.folder || latest.message || '')}`
    : started
      ? `开始识别 ${totalUnits || ''} 组`
      : '等待识别'
  return (
    <aside className="agent-rail">
      <div className="surface-title"><h2>识别进度</h2><span>已记住 {memoryCount} 个别名</span></div>
      <div className="agent-skill"><strong>{skill}</strong><span>{units.length}{totalUnits ? ` / ${totalUnits}` : ''} 组</span></div>
      <div className="event-list">
        {units.slice(-8).reverse().map(event => (
          <div key={event.sequence}>
            <strong>{String(event.payload.title || event.payload.hint_title || event.message || '识别中')}</strong>
            <span>{String(event.payload.folder || '')} · {String(event.payload.files || 1)} 个文件</span>
          </div>
        ))}
      </div>
      {recentMemory.length ? (
        <div className="memory-chips">
          {recentMemory.slice(0, 6).map(item => (
            <span key={item.alias_key}>{item.canonical_title}</span>
          ))}
        </div>
      ) : null}
    </aside>
  )
}
