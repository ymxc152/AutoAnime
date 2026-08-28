import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Dialog } from '../../components/Dialog'
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

const PROPOSAL_LABELS: Record<string, string> = {
  title: '标题',
  media_type: '类型',
  season: '季度',
  episode: '集号',
  release_tag: '发布标签',
  aliases: '别名',
}

const MEDIA_TYPE_LABELS: Record<string, string> = {
  episode: '单集',
  movie: '电影',
  special: '特别篇',
  other: '其他',
}

function visibleText(content: string) {
  return String(content || '')
    .replace(/```(?:json)?[\s\S]*?```/gi, '')
    .replace(/\{[\s\S]*\}/g, '')
    .replace(/\n{2,}/g, '\n')
    .trim()
}

function isAbortError(reason: unknown) {
  return Boolean(reason && typeof reason === 'object' && 'name' in reason && (reason as { name?: string }).name === 'AbortError')
}

function proposalLines(proposal: Record<string, unknown>) {
  return Object.entries(PROPOSAL_LABELS).flatMap(([key, label]) => {
    const value = proposal[key]
    if (value == null || value === '') return []
    if (Array.isArray(value)) {
      const aliases = value.map(item => String(item).trim()).filter(Boolean)
      return aliases.length ? [`${label}：${aliases.join('、')}`] : []
    }
    if (key === 'media_type') {
      const raw = String(value)
      return [`${label}：${MEDIA_TYPE_LABELS[raw] || raw}`]
    }
    return [`${label}：${String(value)}`]
  })
}

export function ReviewChat({ kind, targetId, onApplied }: { kind: AgentKind; targetId: number; onApplied?: () => void }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [pending, setPending] = useState<string[]>([])
  const logRef = useRef<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const sendingRef = useRef(false)
  const stoppedRef = useRef(false)
  const sessionQuery = useQuery({
    queryKey: ['agent-session', kind, targetId],
    queryFn: () => api.post<Session>('/agent/sessions', { kind, target_id: targetId }),
    enabled: open && Boolean(targetId),
  })
  const data = sessionQuery.data
  const session = data?.status === 'open' ? data : undefined
  const send = useMutation({
    mutationFn: async (content: string) => {
      if (!session) throw new Error('纠错会话未就绪')
      sendingRef.current = true
      stoppedRef.current = false
      const controller = new AbortController()
      abortRef.current = controller
      const abortError = () => {
        const error = new Error('Aborted')
        error.name = 'AbortError'
        return error
      }
      try {
        const request = api.post<Session>(`/agent/sessions/${session.id}/messages`, { content }, undefined, { signal: controller.signal })
        const canceled = new Promise<never>((_, reject) => {
          const fail = () => reject(abortError())
          if (controller.signal.aborted) {
            fail()
            return
          }
          controller.signal.addEventListener('abort', fail, { once: true })
        })
        return await Promise.race([request, canceled])
      } catch (reason) {
        if (controller.signal.aborted || stoppedRef.current || isAbortError(reason)) throw abortError()
        throw reason
      } finally {
        sendingRef.current = false
        if (abortRef.current === controller) abortRef.current = null
      }
    },
    onSuccess: value => {
      setPending(current => current.slice(1))
      setError('')
      setNote('')
      client.setQueryData(['agent-session', kind, targetId], value)
    },
    onError: (reason, content) => {
      const aborted = isAbortError(reason) || stoppedRef.current
      setPending(current => current.slice(1))
      if (aborted) {
        setError('')
        setNote('已中断本次回复。')
        client.invalidateQueries({ queryKey: ['agent-session', kind, targetId] })
        return
      }
      setDraft(current => current || content)
      setError(reason instanceof Error ? reason.message : '发送失败')
    },
  })
  const apply = useMutation({
    mutationFn: () => {
      if (!session) throw new Error('纠错会话未就绪')
      return api.post<Session>(`/agent/sessions/${session.id}/apply`)
    },
    onSuccess: async () => {
      abortRef.current?.abort()
      setPending([])
      setError('')
      setNote('提案已应用，可以继续纠正。')
      client.removeQueries({ queryKey: ['agent-session', kind, targetId] })
      await client.invalidateQueries({ queryKey: ['agent-session', kind, targetId] })
      onApplied?.()
    },
    onError: reason => setError(reason instanceof Error ? reason.message : '应用提案失败'),
  })
  const thinking = send.isPending
  const opening = sessionQuery.isFetching && !session
  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    const text = draft.trim()
    if (!text || !session || apply.isPending) return
    setDraft('')
    setError('')
    setNote('')
    setPending(current => [...current, text])
  }
  const stop = () => {
    stoppedRef.current = true
    abortRef.current?.abort()
  }
  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    submit()
  }
  const visible = (data?.messages || []).filter(item => item.role !== 'system' && data?.status === 'open')
  const proposal = session?.proposal && !apply.isPending ? session.proposal : null
  useEffect(() => {
    const node = logRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [visible.length, thinking, pending.length, apply.isPending])
  useEffect(() => {
    if (thinking || sendingRef.current || !session || !pending.length || apply.isPending) return
    sendingRef.current = true
    send.mutate(pending[0])
    // send.mutate is stable; avoid depending on the whole mutation object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thinking, pending, session?.id, apply.isPending])

  return (
    <div className="review-chat">
      <button type="button" className="secondary full" onClick={() => {
        setError('')
        const cached = client.getQueryData<Session>(['agent-session', kind, targetId])
        if (cached && cached.status !== 'open') {
          client.removeQueries({ queryKey: ['agent-session', kind, targetId] })
        }
        setOpen(true)
      }}>问助手</button>
      <Dialog open={open} title="纠错会话" onClose={() => setOpen(false)} size="xl">
        <div className="review-chat-dock">
          {sessionQuery.error ? <div className="form-error">{sessionQuery.error instanceof Error ? sessionQuery.error.message : '无法打开会话'}</div> : null}
          <div className="chat-log" ref={logRef}>
            {!visible.length && !pending.length && !opening && !note ? (
              <p className="muted">直接说明正确标题，例如「应是葬送的芙莉莲」。</p>
            ) : null}
            {visible.map(message => (
              <div className={`chat-bubble ${message.role}`} key={message.id}>
                <span>{visibleText(message.content) || (message.role === 'assistant' ? '已生成提案。' : message.content)}</span>
              </div>
            ))}
            {pending.map((text, index) => (
              <div className="chat-bubble user pending" key={`pending-${index}-${text}`}>
                <span>{text}</span>
              </div>
            ))}
            {thinking ? (
              <div className="chat-bubble assistant pending" aria-live="polite">
                <span>正在思考…</span>
              </div>
            ) : null}
            {apply.isPending ? (
              <div className="chat-bubble assistant pending" aria-live="polite">
                <span>正在应用提案…</span>
              </div>
            ) : null}
            {opening ? (
              <p className="muted chat-status" aria-live="polite">正在打开会话…</p>
            ) : null}
            {note && !thinking ? (
              <p className="muted chat-status" aria-live="polite">{note}</p>
            ) : null}
          </div>
          {proposal ? (
            <div className="change-preview proposal-card">
              <strong>可应用提案</strong>
              {proposalLines(proposal).map(line => <span key={line}>{line}</span>)}
              <button type="button" className="primary" onClick={() => apply.mutate()} disabled={apply.isPending || thinking}>应用提案</button>
            </div>
          ) : null}
          {error ? <div className="form-error">{error}</div> : null}
          <form className="chat-composer" onSubmit={event => submit(event)}>
            <textarea
              aria-label="向助手说明问题"
              rows={2}
              value={draft}
              onChange={event => setDraft(event.target.value)}
              onKeyDown={onComposerKey}
              disabled={!session || apply.isPending}
              placeholder={thinking ? '继续补充，回车加入队列；也可点停止' : apply.isPending ? '正在应用提案…' : '说明正确标题，回车发送，Shift+回车换行'}
            />
            {thinking ? (
              <button type="button" className="secondary" onClick={stop}>停止</button>
            ) : (
              <button type="submit" className="primary" disabled={!draft.trim() || apply.isPending || !session}>发送</button>
            )}
          </form>
        </div>
      </Dialog>
    </div>
  )
}
