import { Activity, AlertTriangle, CheckCircle2, Clock3 } from 'lucide-react'
import { Empty, Status } from '../../components/Page'

export type DashboardData = {
  active_jobs: number; open_reviews: number; conflicts: number; failed_jobs: number
  roots: Array<Record<string, unknown>>; recent_jobs: Array<Record<string, unknown>>
}

export function DashboardView({ data }: { data: DashboardData }) {
  const metrics = [
    ['活动任务', data.active_jobs, Activity, 'info'], ['待审核', data.open_reviews, Clock3, 'warning'],
    ['路径冲突', data.conflicts, AlertTriangle, 'danger'], ['失败任务', data.failed_jobs, CheckCircle2, 'neutral'],
  ] as const
  return <>
    <div className="metric-row">{metrics.map(([label, value, Icon, tone]) => <div className={`metric ${tone}`} key={label}><div><span>{label}</span><strong>{value}</strong></div><Icon size={21} /></div>)}</div>
    <div className="dashboard-grid">
      <section className="surface"><div className="surface-title"><h2>最近任务</h2><span>实时状态</span></div>{data.recent_jobs.length ? <div className="table-wrap"><table><thead><tr><th>任务</th><th>阶段</th><th>进度</th><th>状态</th></tr></thead><tbody>{data.recent_jobs.map(job => <tr key={String(job.id)}><td><strong>#{String(job.id)}</strong> {String(job.job_type)}</td><td>{String(job.current_stage || '等待处理')}</td><td>{Number(job.progress_total) ? `${job.progress_current}/${job.progress_total}` : '—'}</td><td><Status value={String(job.status)} /></td></tr>)}</tbody></table></div> : <Empty />}</section>
      <aside className="rail"><div className="surface-title"><h2>目录健康</h2><span>{data.roots.length} 个根目录</span></div>{data.roots.length ? data.roots.map(root => <div className="root-row" key={String(root.id)}><div><strong>{String(root.path)}</strong><span>{String(root.kind)}</span></div><Status value={String(root.health_status)} /></div>) : <Empty>尚未添加目录</Empty>}<div className="heartbeat"><span className="pulse" /><div><strong>系统心跳正常</strong><small>数据库 WAL · Worker 待命</small></div></div></aside>
    </div>
  </>
}
