import type { PropsWithChildren, ReactNode } from 'react'

export function Page({ title, description, actions, children }: PropsWithChildren<{ title: string; description?: string; actions?: ReactNode }>) {
  return <section className="page"><header className="page-heading"><div><h1>{title}</h1>{description ? <p>{description}</p> : null}</div>{actions ? <div className="page-actions">{actions}</div> : null}</header>{children}</section>
}

export function Status({ value }: { value: string }) {
  const kind = ['failed', 'conflict', 'stale', 'unavailable'].some(item => value.includes(item)) ? 'danger' : ['open', 'queued', 'draft', 'waiting_review'].some(item => value.includes(item)) ? 'warning' : ['running', 'leased', 'executing'].some(item => value.includes(item)) ? 'info' : 'success'
  return <span className={`status ${kind}`}><i />{value}</span>
}

export function Empty({ children = '暂无数据' }: PropsWithChildren) { return <div className="empty">{children}</div> }
