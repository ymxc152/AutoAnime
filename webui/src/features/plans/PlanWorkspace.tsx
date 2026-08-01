import { useMemo, useState } from 'react'
import { ArrowRight, CheckCircle2, FileVideo2 } from 'lucide-react'
import { Empty, Status } from '../../components/Page'

type PlanItem = { id: number; source_path: string; destination_path: string; action: string; reason: string; risk_level: string; execution_status: string; source_size: number }
export type PlanDetail = { id: number; status: string; revision: number; items: PlanItem[] }

const fileName = (path: string) => path.split(/[\\/]/).pop() || path
const formatSize = (size: number) => size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`

export function PlanWorkspace({ plan, onApprove }: { plan: PlanDetail; onApprove: () => void }) {
  const [selectedId, setSelectedId] = useState(plan.items[0]?.id)
  const selected = useMemo(() => plan.items.find(item => item.id === selectedId) || plan.items[0], [plan.items, selectedId])
  const conflicts = plan.items.filter(item => item.execution_status === 'conflict' || item.action === 'conflict').length
  return <div className="plan-layout"><section className="surface plan-table"><div className="plan-toolbar"><div><strong>计划 #{plan.id}</strong><span>修订 {plan.revision} · {plan.items.length} 项</span></div><button className="primary" disabled={conflicts > 0 || !['draft', 'ready'].includes(plan.status)} onClick={onApprove}><CheckCircle2 size={16} />批准并执行</button></div>{plan.items.length ? <div className="table-wrap"><table><thead><tr><th>源文件</th><th>动作</th><th>目标</th><th>大小</th><th>状态</th></tr></thead><tbody>{plan.items.map(item => <tr className={selected?.id === item.id ? 'selected' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><td><div className="file-cell"><FileVideo2 size={17} /><strong>{fileName(item.source_path)}</strong></div></td><td>{item.action}</td><td className="path-cell">{item.destination_path}</td><td>{formatSize(item.source_size)}</td><td><Status value={item.execution_status} /></td></tr>)}</tbody></table></div> : <Empty />}</section><aside className="inspector">{selected ? <><div className="inspector-head"><FileVideo2 size={20} /><div><strong>{fileName(selected.source_path)}</strong><span>计划项 #{selected.id}</span></div></div><dl><dt>处理方式</dt><dd>{selected.action}</dd><dt>风险等级</dt><dd>{selected.risk_level}</dd><dt>原因</dt><dd>{selected.reason || '识别安全，等待执行'}</dd></dl><div className="path-preview"><span>源位置</span><code>{selected.source_path}</code><ArrowRight size={16} /><span>目标位置</span><code>{selected.destination_path}</code></div></> : <Empty />}</aside></div>
}
