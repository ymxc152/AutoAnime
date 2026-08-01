export type JobEvent = {
  id: number
  type: string
  sequence: number
  message: string
  payload: Record<string, unknown>
}

export function parseEventStream(text: string): JobEvent[] {
  return text
    .split(/\r?\n\r?\n/)
    .filter(Boolean)
    .map(block => {
      const lines = block.split(/\r?\n/)
      const id = Number(lines.find(line => line.startsWith('id:'))?.slice(3).trim() || 0)
      const type = lines.find(line => line.startsWith('event:'))?.slice(6).trim() || 'message'
      const data = lines
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n')
      const parsed = JSON.parse(data || '{}') as Partial<JobEvent>
      return {
        id,
        type,
        sequence: Number(parsed.sequence ?? id),
        message: String(parsed.message ?? ''),
        payload: (parsed.payload ?? {}) as Record<string, unknown>,
      }
    })
    .sort((left, right) => left.sequence - right.sequence)
}
