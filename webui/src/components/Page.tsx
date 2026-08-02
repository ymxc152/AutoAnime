import type { PropsWithChildren, ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Page({ title, description, actions, children }: PropsWithChildren<{ title: string; description?: string; actions?: ReactNode }>) {
  return <section className="page"><header className="page-heading"><div><h1>{title}</h1>{description ? <p>{description}</p> : null}</div>{actions ? <div className="page-actions">{actions}</div> : null}</header>{children}</section>
}

const statusLabels: Record<string, string> = {
  queued: '等待中', running: '进行中', completed: '已完成', failed: '失败', open: '待处理',
  draft: '待批准', conflict: '有冲突', healthy: '正常', ready: '已就绪', approved: '已批准',
  rejected: '已拒绝', skip: '跳过', not_preferred_release: '非优选', unknown: '未知',
  pending: '待定', executing: '整理中', stale: '已过期', cancelled: '已取消',
  succeeded: '已完成', leased: '运行中', interrupted: '已中断', active: '启用', inactive: '停用',
  unavailable: '不可用', validated: '已校验', retired: '已停用',
}

export function Status({ value }: { value: string }) {
  const kind = ['failed', 'conflict', 'stale', 'unavailable', 'rejected'].some(item => value.includes(item)) ? 'danger' : ['open', 'queued', 'draft', 'waiting_review'].some(item => value.includes(item)) ? 'warning' : ['running', 'leased', 'executing'].some(item => value.includes(item)) ? 'info' : 'success'
  return <span className={`status ${kind}`} data-status={value}><i />{statusLabels[value] || value}</span>
}

type EmptyProps = PropsWithChildren<{ title?: string; description?: string; cta?: { label: string; to: string } }>
export function Empty({ children = '暂无数据', title, description, cta }: EmptyProps) {
  if (!title && !description && !cta) return <div className="empty">{children}</div>
  return <div className="empty empty-structured"><strong>{title}</strong>{description ? <p>{description}</p> : null}{cta ? <Link className="secondary" to={cta.to}>{cta.label}</Link> : null}</div>
}
