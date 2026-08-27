import { useMemo, useState } from 'react'
import { ArrowRight, CheckCircle2, FileVideo2 } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { Empty, Status } from '../../components/Page'

type Decision = 'approved' | 'rejected' | null
type PlanItem = { id: number; source_path: string; destination_path: string; action: string; reason: string; risk_level: string; execution_status: string; source_size: number; decision?: Decision; reject_reason?: string | null; decided_by?: string | null; decided_at?: string | null }
export type PlanDetail = { id: number; status: string; revision: number; items: PlanItem[] }

const fileName = (path: string) => path.split(/[\\/]/).pop() || path
const formatSize = (size: number) => size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`
const ACTION_LABELS: Record<string, string> = { link: '硬链接', copy: '复制', move: '移动', skip: '跳过', conflict: '冲突', organize: '整理' }
const RISK_LABELS: Record<string, string> = { normal: '普通', high: '高', low: '低' }

function RejectReasonDialog({ itemId, onClose, onConfirm }: { itemId: number; onClose: () => void; onConfirm: (id: number, reason: string) => void }) {
  const [reason, setReason] = useState('')
  const submit = () => {
    const value = reason.trim()
    if (!value) return
    onConfirm(itemId, value)
  }
  return (
    <Dialog open title="拒绝这项整理" description="请填写原因，该项不会被执行。" onClose={onClose}>
      <label>拒绝原因<textarea aria-label="拒绝原因" rows={4} value={reason} onChange={event => setReason(event.target.value)} placeholder="例如：目标路径不对、不是这部番" /></label>
      <div className="form-actions">
        <button type="button" className="primary" onClick={submit}>确认拒绝</button>
        <button type="button" className="secondary" onClick={onClose}>取消</button>
      </div>
    </Dialog>
  )
}

export function PlanWorkspace({ plan, onApprove, onApproveApproved, onApproveItem, onRejectItem }: { plan: PlanDetail; onApprove: () => void; onApproveApproved?: () => void; onApproveItem?: (id: number) => void; onRejectItem?: (id: number, reason: string) => void }) {
  const [selectedId, setSelectedId] = useState(plan.items[0]?.id)
  const [rejectId, setRejectId] = useState<number | null>(null)
  const selected = useMemo(() => plan.items.find(item => item.id === selectedId) || plan.items[0], [plan.items, selectedId])
  const conflicts = plan.items.filter(item => item.execution_status === 'conflict' || item.action === 'conflict').length
  const open = ['draft', 'ready', 'approved'].includes(plan.status)
  const approvedPending = plan.items.filter(item => item.decision === 'approved' && item.execution_status === 'pending' && item.action !== 'skip' && item.action !== 'conflict').length
  const undecided = plan.items.filter(item => !item.decision && item.execution_status === 'pending' && item.action !== 'skip' && item.action !== 'conflict').length
  const canDecide = open
  const actions = (item: PlanItem) => canDecide && item.action !== 'skip' && item.execution_status !== 'completed' ? <div className="row-actions decision-actions">{item.decision !== 'approved' ? <button type="button" className="text-button" onClick={() => onApproveItem?.(item.id)}>批准</button> : null}{item.decision !== 'rejected' ? <button type="button" className="text-button danger" onClick={() => setRejectId(item.id)}>拒绝</button> : null}</div> : null
  return <div className="plan-layout review-layout"><section className="surface plan-table"><div className="plan-toolbar"><div><strong>计划 #{plan.id}</strong><span>修订 {plan.revision} · {plan.items.length} 项</span></div><div className="toolbar-actions"><button className="primary" disabled={conflicts > 0 || !open || approvedPending === 0} onClick={onApproveApproved}><CheckCircle2 size={16} />开始整理已批准项（{approvedPending}）</button>{undecided > 0 ? <button className="secondary" disabled={conflicts > 0 || !['draft', 'ready'].includes(plan.status)} onClick={onApprove}><CheckCircle2 size={16} />全部批准并整理</button> : null}</div></div>{conflicts > 0 ? <div className="risk-warning">计划中有路径冲突，请先处理冲突后再开始整理。</div> : null}{undecided > 0 ? <div className="risk-warning">还有 {undecided} 项未决定：可逐条批准，或用『全部批准并整理』一次处理。</div> : null}{plan.items.length ? <div className="table-wrap"><table><thead><tr><th>源文件</th><th>动作</th><th>目标</th><th>大小</th><th>状态</th><th>操作</th></tr></thead><tbody>{plan.items.map(item => <tr className={selected?.id === item.id ? 'selected' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><td><div className="file-cell"><FileVideo2 size={17} /><strong>{fileName(item.source_path)}</strong></div></td><td>{ACTION_LABELS[item.action] || item.action}</td><td className="path-cell">{item.destination_path}</td><td>{formatSize(item.source_size)}</td><td><Status value={item.decision || item.execution_status} /></td><td onClick={event => event.stopPropagation()}>{actions(item)}</td></tr>)}</tbody></table></div> : <Empty />}</section><aside className="inspector">{selected ? <><div className="inspector-head"><FileVideo2 size={20} /><div><strong>{fileName(selected.source_path)}</strong><span>计划项 #{selected.id}</span></div></div><dl><dt>处理方式</dt><dd>{ACTION_LABELS[selected.action] || selected.action}</dd><dt>风险等级</dt><dd>{RISK_LABELS[selected.risk_level] || selected.risk_level}</dd><dt>决定状态</dt><dd><Status value={selected.decision || selected.execution_status} /></dd><dt>原因</dt><dd>{selected.reason || '识别安全，等待执行'}</dd>{selected.decision === 'rejected' ? <><dt>拒绝原因</dt><dd>{selected.reject_reason || '—'}</dd></> : null}</dl>{actions(selected)}<div className="path-preview"><span>源位置</span><code>{selected.source_path}</code><ArrowRight size={16} /><span>目标位置</span><code>{selected.destination_path}</code></div></> : <Empty />}</aside>
    {rejectId !== null ? <RejectReasonDialog itemId={rejectId} onClose={() => setRejectId(null)} onConfirm={(id, reason) => { onRejectItem?.(id, reason); setRejectId(null) }} /> : null}
  </div>
}
