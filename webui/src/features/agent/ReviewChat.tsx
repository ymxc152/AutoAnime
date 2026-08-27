import { FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'

type AgentKind = 'review' | 'library'
type Message = { id: number; role: string; content: string; proposal?: Record<string, unknown> | null }
type Session = {
  id: number
  kind: AgentKind
  target_id: number
  status: string
  proposal: Record<string, unknown> | null
  messages: Message[]
}

export function ReviewChat({ kind, targetId, onApplied }: { kind: AgentKind; targetId: number; onApplied?: () => void }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const sessionQuery = useQuery({
    queryKey: ['agent-session', kind, targetId],
    queryFn: () => api.post<Session>('/agent/sessions', { kind, target_id: targetId }),
    enabled: open && Boolean(targetId),
  })
  const session = sessionQuery.data
  const send = useMutation({
    mutationFn: (content: string) => api.post<Session>(`/agent/sessions/${session?.id}/messages`, { content }),
    onSuccess: async value => {
      setDraft('')
      setError('')
      client.setQueryData(['agent-session', kind, targetId], value)
    },
    onError: reason => setError(reason instanceof Error ? reason.message : '发送失败'),
  })
  const apply = useMutation({
    mutationFn: () => api.post<Session>(`/agent/sessions/${session?.id}/apply`),
    onSuccess: async () => {
      setError('')
      setOpen(false)
      await client.invalidateQueries({ queryKey: ['agent-session', kind, targetId] })
      onApplied?.()
    },
    onError: reason => setError(reason instanceof Error ? reason.message : '应用提案失败'),
  })
  const abandon = useMutation({
    mutationFn: () => api.post<Session>(`/agent/sessions/${session?.id}/abandon`),
    onSuccess: async () => {
      setOpen(false)
      await client.invalidateQueries({ queryKey: ['agent-session', kind, targetId] })
    },
    onError: reason => setError(reason instanceof Error ? reason.message : '结束会话失败'),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!draft.trim() || !session) return
    send.mutate(draft.trim())
  }
  const visible = (session?.messages || []).filter(item => item.role !== 'system')
  return (
    <div className="review-chat">
      {!open ? (
        <button type="button" className="secondary" onClick={() => setOpen(true)}>问 Agent</button>
      ) : (
        <div className="review-chat-dock">
          <div className="surface-title">
            <h2>纠错会话</h2>
            <button type="button" className="text-button" onClick={() => abandon.mutate()} disabled={!session || abandon.isPending}>结束会话</button>
          </div>
          {sessionQuery.error ? <p className="error-copy">{sessionQuery.error instanceof Error ? sessionQuery.error.message : '无法打开会话'}</p> : null}
          <div className="chat-log">
            {visible.map(message => (
              <div className={`chat-bubble ${message.role}`} key={message.id}>
                <strong>{message.role === 'assistant' ? 'Agent' : '你'}</strong>
                <span>{message.content}</span>
              </div>
            ))}
          </div>
          {session?.proposal ? (
            <div className="change-preview">
              <strong>可应用提案</strong>
              <span>{JSON.stringify(session.proposal)}</span>
              <button type="button" className="primary" onClick={() => apply.mutate()} disabled={apply.isPending}>应用提案</button>
            </div>
          ) : null}
          {error ? <div className="form-error">{error}</div> : null}
          <form className="inline-form" onSubmit={submit}>
            <input aria-label="向 Agent 说明问题" value={draft} onChange={event => setDraft(event.target.value)} placeholder="例如：标题识别错了，应是葬送的芙莉莲" />
            <button className="primary" disabled={!draft.trim() || send.isPending || !session}>发送</button>
          </form>
        </div>
      )}
    </div>
  )
}
