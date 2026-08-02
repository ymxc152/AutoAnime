import { Activity, AlertTriangle, CheckCircle2, Clock3 } from 'lucide-react'
import { Link } from 'react-router-dom'
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
  const next = data.roots.length === 0
    ? { label: '开始设置目录', to: '/scan' }
    : data.open_reviews > 0
      ? { label: `处理 ${data.open_reviews} 个待确认项`, to: '/inbox?tab=reviews' }
      : data.conflicts > 0
        ? { label: '查看需要处理的冲突', to: '/inbox?tab=plans' }
        : data.active_jobs > 0
          ? { label: '查看扫描进度', to: '/activity?tab=jobs' }
          : { label: '开始新的扫描', to: '/scan' }
  return <>
    <div className="metric-row">{metrics.map(([label, value, Icon, tone]) => <div className={`metric ${tone}`} key={label}><div><span>{label}</span><strong>{value}</strong></div><Icon size={21} /></div>)}</div>
    <section className="surface next-action"><div><h2>建议下一步</h2><p>根据当前状态继续最需要的操作。</p></div><div className="row-actions"><Link className="primary" to={next.to}>{next.label}</Link>{data.roots.length > 0 && !data.open_reviews && !data.conflicts && !data.active_jobs ? <Link className="secondary" to="/library">查看资料库</Link> : null}</div></section>
    <div className="dashboard-grid">
      <section className="surface"><div className="surface-title"><h2>最近任务</h2><Link className="text-button" to="/activity">查看全部记录</Link></div>{data.recent_jobs.length ? <div className="table-wrap"><table><thead><tr><th>任务</th><th>阶段</th><th>进度</th><th>状态</th></tr></thead><tbody>{data.recent_jobs.map(job => <tr key={String(job.id)}><td><strong>#{String(job.id)}</strong> {String(job.job_type)}</td><td>{String(job.current_stage || '等待处理')}</td><td>{Number(job.progress_total) ? `${job.progress_current}/${job.progress_total}` : '—'}</td><td><Status value={String(job.status)} /></td></tr>)}</tbody></table></div> : <Empty />}</section>
      <aside className="rail"><div className="surface-title"><h2>目录健康</h2><span>{data.roots.length} 个根目录</span></div>{data.roots.length ? data.roots.map(root => <div className="root-row" key={String(root.id)}><div><strong>{String(root.path)}</strong><span>{String(root.kind)}</span></div><Status value={String(root.health_status)} /></div>) : <Empty>尚未添加目录</Empty>}<div className="heartbeat"><span className="pulse" /><div><strong>系统心跳正常</strong><small>数据库 WAL · Worker 待命</small></div></div></aside>
    </div>
  </>
}
