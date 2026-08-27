import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Plus, RefreshCw, RotateCcw, ScanSearch, Save, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { parseEventStream } from '../api/events'
import { Empty, Page, Status } from '../components/Page'
import { AgentRail } from '../features/agent/AgentRail'
import { ReviewChat } from '../features/agent/ReviewChat'
import { QbWebhookWizard } from '../features/automation/QbWebhookWizard'
import { DashboardData, DashboardView } from '../features/dashboard/DashboardView'
import { PlanDetail, PlanWorkspace } from '../features/plans/PlanWorkspace'

type Item = Record<string, any>
type ListResponse = { items: Item[] }
const useList = (key: string, path: string, interval = 5000, enabled = true) => useQuery({ queryKey: [key], queryFn: () => api.get<ListResponse>(path), refetchInterval: interval, enabled })
const MODE_LABELS: Record<string, string> = { link: '硬链接', copy: '复制', move: '移动' }
const POLICY_LABELS: Record<string, string> = { review_all: '全部审核', auto_apply_safe: '安全项自动', dry_run: '仅预览' }
const KIND_LABELS: Record<string, string> = { source: '下载源', library: '媒体库', operations: '操作日志', execute: '整理', correction: '纠正', interval: '间隔', daily: '每天', backup: '备份' }
const JOB_TYPE_LABELS: Record<string, string> = { scan: '扫描', execute_plan: '整理执行', rollback_operation: '回滚' }
const queryErrorMessage = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback

export function DashboardPage() {
  const query = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get<DashboardData>('/dashboard'), refetchInterval: 5000 })
  return <Page title="首页" description="扫描、确认与文件整理的实时运行状态">{query.data ? <DashboardView data={query.data} /> : <Empty>{query.error ? '无法读取系统状态' : '正在载入…'}</Empty>}</Page>
}

const defaultProfile = { name: '', source_root_id: '', library_root_id: '', mode: 'link', execution_policy: 'review_all', min_confidence: 86, stability_seconds: 30, watch_enabled: false, enabled: true }

export function ProfileForm({ initial, roots, editing = false, onSave, onCancel }: { initial: Item; roots: Item[]; editing?: boolean; onSave: (value: Item) => void; onCancel?: () => void }) {
  const [value, setValue] = useState<Item>({ ...initial, watch_enabled: Boolean(initial.watch_enabled), enabled: Boolean(initial.enabled) })
  const [showAdvanced, setShowAdvanced] = useState(false)
  const sourceRoots = roots.filter(item => item.kind === 'source' && item.enabled !== 0)
  const libraryRoots = roots.filter(item => item.kind === 'library' && item.enabled !== 0)
  const change = (key: string, next: unknown) => setValue(current => ({ ...current, [key]: next }))
  const submit = (event: FormEvent) => { event.preventDefault(); onSave({ ...value, source_root_id: Number(value.source_root_id), library_root_id: Number(value.library_root_id), min_confidence: Number(value.min_confidence), stability_seconds: Number(value.stability_seconds) }) }
  return <form className="profile-form profile-editor" onSubmit={submit}>
    {editing ? <div className="editing-header"><strong>正在编辑扫描方案「{value.name}」</strong><span>修改后点击「保存配置」生效</span></div> : null}
    <label>方案名称<input aria-label="配置名称" value={value.name} onChange={event => change('name', event.target.value)} /></label>
    <label>下载源<select aria-label="下载源" value={value.source_root_id} onChange={event => change('source_root_id', event.target.value)}><option value="">选择下载源</option>{sourceRoots.map(root => <option key={root.id} value={root.id}>{root.path}</option>)}</select></label><label>媒体库<select aria-label="媒体库" value={value.library_root_id} onChange={event => change('library_root_id', event.target.value)}><option value="">选择媒体库</option>{libraryRoots.map(root => <option key={root.id} value={root.id}>{root.path}</option>)}</select></label>
    <label>文件模式<select aria-label="文件模式" value={value.mode} onChange={event => change('mode', event.target.value)}><option value="link">硬链接（保种推荐）</option><option value="copy">复制</option><option value="move">移动</option></select></label>
    {value.mode === 'move' ? <div className="risk-warning">源目录中的文件会被移走。第一次使用建议选择硬链接或复制。</div> : null}
    <label>执行策略<select aria-label="执行策略" value={value.execution_policy} onChange={event => change('execution_policy', event.target.value)}><option value="review_all">全部审核（推荐起步）</option><option value="auto_apply_safe">安全项自动执行</option><option value="dry_run">仅预览</option></select></label>
    <button type="button" className="more-options" aria-expanded={showAdvanced} onClick={() => setShowAdvanced(value => !value)}>{showAdvanced ? '收起更多选项' : '更多选项'}</button>
    {showAdvanced ? <div className="advanced-fields"><label>最低置信度<input aria-label="最低置信度" type="number" min="0" max="100" value={value.min_confidence} onChange={event => change('min_confidence', event.target.value)} /></label><label>稳定等待秒数<input aria-label="稳定等待秒数" type="number" min="0" value={value.stability_seconds} onChange={event => change('stability_seconds', event.target.value)} /></label><label className="check-field"><input type="checkbox" checked={Boolean(value.watch_enabled)} onChange={event => change('watch_enabled', event.target.checked)} />启用目录监听</label><label className="check-field"><input type="checkbox" checked={Boolean(value.enabled)} onChange={event => change('enabled', event.target.checked)} />启用此配置</label></div> : null}
    <div className="form-actions"><button className="primary" disabled={!value.name || !value.source_root_id || !value.library_root_id}><Save size={16} />{editing ? '保存配置' : '创建扫描方案'}</button>{onCancel ? <button type="button" className="secondary" onClick={onCancel}>取消</button> : null}</div>
  </form>
}
export function ProfilesPage() {
  const roots = useList('roots', '/roots'); const profiles = useList('profiles', '/profiles'); const client = useQueryClient()
  const [kind, setKind] = useState('source'); const [path, setPath] = useState(''); const [editing, setEditing] = useState<Item | null>(null); const [pathError, setPathError] = useState(''); const [actionError, setActionError] = useState(''); const [scanSuccess, setScanSuccess] = useState(false)
  const editorRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => { if (editing && editorRef.current) try { editorRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) } catch { /* jsdom 无实现 */ } }, [editing])
  const addRoot = useMutation({ mutationFn: () => api.post('/roots', { kind, path }), onSuccess: async () => { setPath(''); setPathError(''); setActionError(''); await client.invalidateQueries({ queryKey: ['roots'] }) }, onError: reason => setPathError(reason instanceof Error ? reason.message : '添加失败') })
  const pickFolder = useMutation({ mutationFn: () => api.post<{ path: string | null; cancelled: boolean }>('/system/pick-folder', { title: kind === 'library' ? '选择媒体库文件夹' : kind === 'operations' ? '选择操作日志文件夹' : '选择下载源文件夹', initial_directory: path || undefined }), onSuccess: result => { setPathError(''); if (!result.cancelled && result.path) setPath(result.path) }, onError: reason => { const msg = reason instanceof Error ? reason.message : ''; setPathError(/only available on the local machine/i.test(msg) ? '文件夹选择仅本机可用（请用 127.0.0.1 打开控制台，或直接粘贴路径）' : msg || '无法打开系统文件夹选择器') } })
  const validateRoot = useMutation({ mutationFn: (id: number) => api.post(`/roots/${id}/validate`), onSuccess: () => client.invalidateQueries({ queryKey: ['roots'] }), onError: reason => setActionError(reason instanceof Error ? reason.message : '验证失败') })
  const toggleRoot = useMutation({ mutationFn: (root: Item) => api.patch(`/roots/${root.id}`, { patch: { enabled: !Boolean(root.enabled) } }), onSuccess: () => client.invalidateQueries({ queryKey: ['roots'] }), onError: reason => setActionError(reason instanceof Error ? reason.message : '切换失败') })
  const deleteRoot = useMutation({ mutationFn: (root: Item) => api.delete(`/roots/${root.id}`), onSuccess: async () => { setActionError(''); await client.invalidateQueries({ queryKey: ['roots'] }) }, onError: reason => setActionError(reason instanceof Error ? reason.message : '删除失败') })
  const addProfile = useMutation({ mutationFn: (profile: Item) => api.post('/profiles', profile), onSuccess: () => client.invalidateQueries({ queryKey: ['profiles'] }), onError: reason => setActionError(reason instanceof Error ? reason.message : '创建扫描方案失败') })
  const updateProfile = useMutation({ mutationFn: ({ profile, patch }: { profile: Item; patch: Item }) => api.patch(`/profiles/${profile.id}`, { revision: profile.revision, patch }), onSuccess: async () => { setActionError(''); setEditing(null); await client.invalidateQueries({ queryKey: ['profiles'] }) }, onError: reason => setActionError(reason instanceof Error ? reason.message : '保存失败') })
  const deleteProfile = useMutation({ mutationFn: (profile: Item) => api.delete(`/profiles/${profile.id}`, { revision: profile.revision }), onSuccess: async () => { setActionError(''); setEditing(null); await client.invalidateQueries({ queryKey: ['profiles'] }) }, onError: reason => setActionError(reason instanceof Error ? reason.message : '删除失败') })
  const scan = useMutation({ mutationFn: (id: number) => api.post('/jobs/scans', { profile_id: id, paths: [] }, { 'Idempotency-Key': `manual-${id}-${Date.now()}` }), onSuccess: () => setScanSuccess(true), onError: reason => setActionError(reason instanceof Error ? reason.message : '创建扫描任务失败') })
  const rootPath = (id: unknown) => roots.data?.items.find(root => Number(root.id) === Number(id))?.path ?? `目录 #${id}`
  return <Page title="目录与扫描" description="先选择下载目录和媒体库，再创建一个扫描方案。" actions={<button className="secondary" onClick={() => roots.refetch()}><RefreshCw size={16} />刷新状态</button>}>
    <div className="steps"><span><b>1</b>添加下载源与媒体库目录</span><span><b>2</b>创建扫描方案</span><span><b>3</b>开始扫描</span></div>
    {scanSuccess ? <div className="success-note"><span>扫描任务已创建。文件较多时可能需要一些时间。</span><div className="row-actions"><Link className="primary" to="/inbox">查看待处理</Link><Link className="secondary" to="/activity?tab=jobs">查看扫描进度</Link></div></div> : null}
    {actionError ? <div className="form-error">{actionError}</div> : null}
    <div className="split"><section className="surface"><div className="surface-title"><h2>目录</h2><span>本机可点“浏览”选择文件夹</span></div><form className="inline-form" onSubmit={event => { event.preventDefault(); addRoot.mutate() }}><select aria-label="目录类型" value={kind} onChange={event => setKind(event.target.value)}><option value="source">下载源</option><option value="library">媒体库</option><option value="operations">操作日志（更多）</option></select><input aria-label="目录路径" value={path} onChange={event => setPath(event.target.value)} placeholder="F:\动漫下载" /><button type="button" className="secondary" onClick={() => pickFolder.mutate()} disabled={pickFolder.isPending}>{pickFolder.isPending ? '选择中…' : '浏览…'}</button><button className="primary" disabled={!path}><Plus size={16} />添加</button></form>{pathError ? <div className="form-error form-indent">{pathError}</div> : null}<p className="muted form-indent">局域网浏览器无法弹出服务器本机对话框，请直接粘贴路径。可添加多个下载源和多个媒体库；每个扫描方案选一对；多个方案可以指向同一媒体库。</p><DataTable items={roots.data?.items || []} columns={['kind', 'path', 'health_status', 'enabled']} action={root => <div className="row-actions"><button className="text-button" onClick={() => validateRoot.mutate(root.id)}>验证</button><button className="text-button" onClick={() => toggleRoot.mutate(root)}>{root.enabled ? '停用' : '启用'}</button><button className="text-button danger" onClick={() => { if (window.confirm(`删除目录“${root.path}”？`)) deleteRoot.mutate(root) }}><Trash2 size={13} />删除</button></div>} /></section>
      <section className="surface"><div className="surface-title"><h2>扫描方案</h2><span>建议先用“全部审核”</span></div><div ref={editorRef}>{editing ? <ProfileForm key={editing.id} editing initial={editing} roots={roots.data?.items || []} onCancel={() => setEditing(null)} onSave={patch => updateProfile.mutate({ profile: editing, patch: { name: patch.name, source_root_id: patch.source_root_id, library_root_id: patch.library_root_id, mode: patch.mode, execution_policy: patch.execution_policy, min_confidence: patch.min_confidence, stability_seconds: patch.stability_seconds, watch_enabled: patch.watch_enabled, enabled: patch.enabled } })} /> : <ProfileForm initial={defaultProfile} roots={roots.data?.items || []} onSave={profile => addProfile.mutate(profile)} />}</div>{profiles.error ? <Empty title="无法读取扫描方案" description={queryErrorMessage(profiles.error, '请稍后重试')} /> : profiles.data?.items.length ? profiles.data.items.map(profile => <div className={editing?.id === profile.id ? 'profile-row active' : 'profile-row'} key={profile.id}><div><strong title={profile.name}>{profile.name}</strong><span>{MODE_LABELS[String(profile.mode)] || profile.mode} · {POLICY_LABELS[String(profile.execution_policy)] || profile.execution_policy} · 阈值 {profile.min_confidence}%</span><span>{rootPath(profile.source_root_id)} → {rootPath(profile.library_root_id)}</span></div><div className="row-actions"><button className="text-button" onClick={() => setEditing(profile)}>编辑</button><button className="secondary" onClick={() => scan.mutate(profile.id)} disabled={!profile.enabled}><ScanSearch size={16} />手动扫描</button><button className="text-button danger" onClick={() => { if (window.confirm(`删除扫描方案“${profile.name}”？`)) deleteProfile.mutate(profile) }}><Trash2 size={13} />删除</button></div></div>) : <Empty title="还没有扫描方案" description="添加下载源和媒体库后创建第一个扫描方案。" />}<p className="muted form-indent">开启 AI 可减少待确认项（设置 → 常用 → AI 识别）</p></section></div>
  </Page>
}
function JobsPanel({ active = true }: { active?: boolean }) {
  const query = useList('jobs', '/jobs', 5000, active); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null); const [jobError, setJobError] = useState('')
  const live = Boolean(selected && ['queued', 'leased', 'running'].includes(String(selected.status)))
  const events = useQuery({ queryKey: ['job-events', selected?.id], queryFn: async () => parseEventStream(await api.text(`/jobs/${selected?.id}/events`)), enabled: active && Boolean(selected), refetchInterval: live ? 2000 : false })
  useEffect(() => {
    if (!selected) return
    const current = query.data?.items.find(item => item.id === selected.id)
    if (current && current.status !== selected.status) setSelected(current)
  }, [query.data?.items, selected])
  const memory = useQuery({ queryKey: ['memory'], queryFn: () => api.get<ListResponse>('/memory'), enabled: active && Boolean(selected), refetchInterval: live ? 4000 : false })
  const cancel = useMutation({ mutationFn: (id: number) => api.post(`/jobs/${id}/cancel`), onSuccess: () => client.invalidateQueries({ queryKey: ['jobs'] }), onError: reason => setJobError(reason instanceof Error ? reason.message : '取消任务失败') })
  if (query.error) return <Empty title="无法读取运行记录" description={queryErrorMessage(query.error, '请稍后重试')} />
  if (!query.data?.items.length) return <Empty title="还没有运行记录" description="开始第一次扫描后，可以在这里查看进度和结果。" />
  return <div className="plan-layout">{jobError ? <div className="form-error">{jobError}</div> : null}<section className="surface"><DataTable items={query.data.items} columns={['id', 'job_type', 'current_stage', 'progress_current', 'status', 'created_at']} onRow={setSelected} selectedId={selected?.id} action={item => ['queued', 'leased', 'running'].includes(item.status) ? <button className="text-button" onClick={() => cancel.mutate(item.id)}>安全取消</button> : null} /></section><aside className="inspector">{selected ? <><h2>任务 #{selected.id}</h2><Status value={selected.status} /><dl><dt>类型</dt><dd>{JOB_TYPE_LABELS[String(selected.job_type)] || selected.job_type}</dd><dt>阶段</dt><dd>{selected.current_stage || '—'}</dd><dt>错误</dt><dd>{selected.error_summary || '—'}</dd></dl><AgentRail events={events.data || []} memoryCount={memory.data?.items.length || 0} recentMemory={(memory.data?.items || []).slice(0, 6).map(item => ({ alias_key: String(item.alias_key || ''), canonical_title: String(item.canonical_title || ''), source: String(item.source || '') }))} /><h3>事件记录</h3><div className="event-list">{events.data?.length ? events.data.map(event => <div key={event.sequence}><strong>#{event.sequence} {event.type}</strong><span>{event.message || JSON.stringify(event.payload)}</span></div>) : <Empty>暂无事件</Empty>}</div></> : <Empty>选择任务查看事件</Empty>}</aside></div>
}
export function JobsPage() { return <Page title="任务进度"><JobsPanel /></Page> }
type MediaType = 'episode' | 'movie' | 'special'

function reviewPayload(review: Item) {
  return review.payload?.resolution && typeof review.payload.resolution === 'object'
    ? review.payload.resolution
    : (review.payload || {})
}

function inferMediaType(payload: Item): MediaType {
  if (payload.media_type === 'episode' || payload.media_type === 'movie' || payload.media_type === 'special') return payload.media_type
  if (payload.is_movie) return 'movie'
  if (Number(payload.season) === 0 || (typeof payload.episode === 'string' && /^sp/i.test(payload.episode))) return 'special'
  return 'episode'
}

const LANG_LABELS: Record<string, string> = { romaji: '罗马音', ja: '日文', en: '英文', 'zh-cn': '简体', 'zh-tw': '繁体' }

function candidateLabel(cand: Item): string {
  if (cand.source === 'aiparse') return `AI·${LANG_LABELS[String(cand.lang)] || String(cand.lang || '')}`
  if (cand.source === 'metadata') {
    const provider = cand.provider === 'bgm' ? 'bgm' : cand.provider === 'tmdb' ? 'TMDB' : String(cand.provider || '')
    return Number.isFinite(Number(cand.confidence)) ? `${provider} · ${Math.round(Number(cand.confidence) * 100)}%` : provider
  }
  return '文件名'
}

function inputValue(value: unknown) {
  return value === null || value === undefined ? '' : String(value)
}

function structuredEpisode(value: string): number | string {
  const trimmed = value.trim()
  if (/^\d+$/.test(trimmed)) return Number(trimmed)
  if (/^\d+\.\d+$/.test(trimmed)) return Number(trimmed)
  return trimmed
}

function episodeToken(value: string) {
  const parsed = structuredEpisode(value)
  return typeof parsed === 'number' && Number.isInteger(parsed)
    ? String(parsed).padStart(2, '0')
    : String(parsed)
}

export function ReviewResolutionForm({ review, onSubmit, submitting = false }: { review: Item; onSubmit: (resolution: Item) => void; submitting?: boolean }) {
  const payload = reviewPayload(review)
  const [title, setTitle] = useState(inputValue(payload.title || payload.canonical_title))
  const [mediaType, setMediaType] = useState<MediaType>(() => inferMediaType(payload))
  const [season, setSeason] = useState(inputValue(payload.season ?? (inferMediaType(payload) === 'special' ? 0 : '')))
  const [episode, setEpisode] = useState(inputValue(payload.episode))
  const [releaseTag, setReleaseTag] = useState(inputValue(payload.release_tag))
  const [manualLock, setManualLock] = useState(payload.manual_lock === undefined ? true : Boolean(payload.manual_lock))
  const evidence = payload.evidence ?? review.payload ?? {}
  const candidates = Array.isArray(payload.candidates) ? payload.candidates : []
  const sourcePath = typeof payload.source === 'string' ? payload.source : ''
  const sourceName = sourcePath.split(/[\\/]/).pop() || sourcePath
  const evidenceList = Array.isArray(evidence) ? evidence : []
  const confidenceValue = Number(payload.confidence)
  const confidencePct = Number.isFinite(confidenceValue) ? Math.round(confidenceValue * 100) : null
  const recogLabel = mediaType === 'movie' ? '电影' : mediaType === 'special' ? (() => { const token = episodeToken(String(payload.episode ?? '1')); return /^sp/i.test(token) ? token : `SP${token}` })() : `S${String(Number(payload.season ?? 1)).padStart(2, '0')} · E${episodeToken(String(payload.episode ?? '0'))}`
  const extension = typeof payload.source === 'string' ? (payload.source.match(/\.[^./\\]+$/)?.[0] || '.ext') : '.ext'
  const version = releaseTag.trim() ? ` [${releaseTag.trim()}]` : ''
  const safeTitle = title.trim() || '未命名标题'
  const token = episodeToken(episode || '0')
  const preview = mediaType === 'movie'
    ? `${safeTitle}/${safeTitle}${version}${extension}`
    : mediaType === 'special'
      ? `${safeTitle}/Specials/${/^sp/i.test(token) ? token : `SP${token}`} - ${safeTitle}${version}${extension}`
      : `${safeTitle}/Season ${String(Number(season || 0)).padStart(2, '0')}/S${String(Number(season || 0)).padStart(2, '0')}E${token} - ${safeTitle}${version}${extension}`
  const complete = Boolean(title.trim()) && (mediaType === 'movie' || (season !== '' && episode.trim() !== ''))

  const changeMediaType = (nextType: MediaType) => {
    if (nextType === 'movie') {
      setSeason('')
      setEpisode('')
    } else if (nextType === 'special') {
      setSeason('0')
      setEpisode(current => /^sp/i.test(current.trim()) ? current : 'SP01')
    } else if (nextType === 'episode' && mediaType !== 'episode') {
      setSeason('1')
      setEpisode('')
    }
    setMediaType(nextType)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const resolution: Item = {
      title: title.trim(),
      media_type: mediaType,
      release_tag: releaseTag.trim(),
      manual_lock: manualLock,
    }
    if (mediaType !== 'movie') {
      resolution.season = Number(season)
      resolution.episode = structuredEpisode(episode)
    }
    onSubmit(resolution)
  }

  return <form className="review-resolution-form" onSubmit={submit}>
    <div className="evidence-source"><strong>来源文件</strong><span>{sourceName || '—'}</span>{sourcePath && sourcePath !== sourceName ? <small>{sourcePath}</small> : null}</div>
    <div className="evidence-result"><strong>识别结果</strong><span>{payload.title || '—'}{payload.season !== undefined && payload.season !== null && payload.season !== '' ? ` · ${recogLabel}` : ''}</span>{confidencePct !== null ? <small>置信度 {confidencePct}%</small> : null}</div>
    <h3>识别依据</h3>
    <div className="evidence-list">{evidenceList.length ? evidenceList.map((item, index) => <div className="evidence-item" key={index}><span className="evidence-agent">{String(item.agent || '')}</span><span className="evidence-value">{String(item.value ?? '')}</span><span className="evidence-conf">{item.confidence !== undefined ? `${Math.round(Number(item.confidence) * 100)}%` : ''}</span></div>) : <p className="muted">无识别证据</p>}</div>
    <details className="evidence-raw"><summary>原始证据 JSON</summary><pre className="evidence">{JSON.stringify(evidence, null, 2)}</pre></details>
    {candidates.length ? (
      <div className="review-candidates">
        <strong>候选标题（点击选用，或直接在下方输入自定义名称）</strong>
        <div className="review-candidates-list">
          {candidates.map((cand: Item, index: number) => (
            <button
              key={index}
              type="button"
              className={`review-candidate${title.trim() === cand.title ? ' active' : ''}`}
              onClick={() => setTitle(String(cand.title || ''))}
            >
              <span>{String(cand.title || '')}</span>
              <small>{candidateLabel(cand)}</small>
              {cand.is_anime === false ? <em>真人版?</em> : null}
            </button>
          ))}
        </div>
      </div>
    ) : null}
    <label>标题<input aria-label="标题" value={title} onChange={event => setTitle(event.target.value)} /></label>
    <label>媒体类型<select aria-label="媒体类型" value={mediaType} onChange={event => changeMediaType(event.target.value as MediaType)}><option value="episode">单集</option><option value="movie">电影</option><option value="special">特别篇 / SP</option></select></label>
    {mediaType !== 'movie' ? <><label>季度<input aria-label="季度" type="number" min="0" step="1" value={season} onChange={event => setSeason(event.target.value)} /></label><label>集号<input aria-label="集号" value={episode} onChange={event => setEpisode(event.target.value)} placeholder={mediaType === 'special' ? '例如 SP01、0、12.5' : '例如 12、12.5、12A'} /></label></> : null}
    <label>版本 / 发布标签<input aria-label="版本 / 发布标签" value={releaseTag} onChange={event => setReleaseTag(event.target.value)} placeholder="例如 WEB-DL、BDRip" /></label>
    <label className="check-field"><input type="checkbox" checked={manualLock} onChange={event => setManualLock(event.target.checked)} />人工锁</label>
    <div className="change-preview"><strong>目标路径预览</strong><span>{preview}</span><small>实际目标会按媒体库根目录、文件扩展名和同集版本冲突规则生成。</small></div>
    <button className="primary full" disabled={!complete || submitting}><Save size={16} />确认信息并生成计划</button>
  </form>
}

function ReviewsPanel({ active = true, onResolved }: { active?: boolean; onResolved?: () => void }) {
  const query = useList('reviews', '/reviews', 5000, active); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null)
  const resolve = useMutation({ mutationFn: (resolution: Item) => api.post(`/reviews/${selected?.id}/resolve`, { resolution }), onSuccess: async () => { setSelected(null); await client.invalidateQueries({ queryKey: ['reviews'] }); await client.invalidateQueries({ queryKey: ['plans'] }); onResolved?.() } })
  if (query.error) return <Empty title="无法读取待确认项" description={queryErrorMessage(query.error, '请稍后重试')} />
  if (!query.data?.items.length) return <Empty title="没有需要人工确认的文件" description="系统目前没有发现标题、季集或路径问题。" cta={{ label: '查看整理计划', to: '/inbox?tab=plans' }} />
  const items = query.data.items.map((item: Item) => ({ ...item, source_file: item.payload?.source ? String(item.payload.source).split(/[\\/]/).pop() : '—' }))
  return <div className="plan-layout"><section className="surface"><DataTable items={items} columns={['id', 'source_file', 'review_type', 'status']} onRow={setSelected} selectedId={selected?.id} /></section><aside className="inspector">{selected ? <><h2>确认 #{selected.id}</h2><ReviewChat key={`chat-${selected.id}`} kind="review" targetId={Number(selected.id)} onApplied={() => { setSelected(null); client.invalidateQueries({ queryKey: ['reviews'] }); client.invalidateQueries({ queryKey: ['plans'] }); onResolved?.() }} /><ReviewResolutionForm key={selected.id} review={selected} onSubmit={resolution => resolve.mutate(resolution)} submitting={resolve.isPending} />{resolve.error ? <p className="error-copy">{resolve.error.message}</p> : null}</> : <Empty>选择一条待确认记录</Empty>}</aside></div>
}

function PlansPanel({ active = true }: { active?: boolean }) {
  const list = useList('plans', '/plans', 5000, active); const [id, setId] = useState<number | null>(null); const [manualSelection, setManualSelection] = useState(false); const [planError, setPlanError] = useState(''); const detail = useQuery({ queryKey: ['plan', id], queryFn: () => api.get<PlanDetail>(`/plans/${id}`), enabled: active && id !== null }); const client = useQueryClient()
  const refresh = async () => { await client.invalidateQueries({ queryKey: ['plans'] }); await detail.refetch() }
  const fail = (reason: unknown) => setPlanError(reason instanceof Error ? reason.message : '操作失败')
  const approve = useMutation({ mutationFn: () => api.post(`/plans/${id}/approve`), onSuccess: refresh, onError: fail })
  const approveApproved = useMutation({ mutationFn: () => api.post(`/plans/${id}/execute-approved`), onSuccess: refresh, onError: fail })
  const approveItem = useMutation({ mutationFn: (itemId: number) => api.post(`/plans/${id}/items/${itemId}/approve`), onSuccess: refresh, onError: fail })
  const rejectItem = useMutation({ mutationFn: ({ itemId, reason }: { itemId: number; reason: string }) => api.post(`/plans/${id}/items/${itemId}/reject`, { reason }), onSuccess: refresh, onError: fail })
  const dismiss = useMutation({ mutationFn: (planId: number) => api.delete(`/plans/${planId}`), onSuccess: async () => { setManualSelection(true); setId(null); await client.invalidateQueries({ queryKey: ['plans'] }) }, onError: fail })
  const dismissible = (status: string) => !['draft', 'ready', 'approved', 'executing', 'completed'].includes(status)
  useEffect(() => { const newest = list.data?.items[0]?.id; if (!manualSelection && newest && id !== Number(newest)) setId(Number(newest)) }, [id, list.data?.items, manualSelection])
  if (list.error) return <Empty title="无法读取整理计划" description={queryErrorMessage(list.error, '请稍后重试')} />
  if (!list.data?.items.length) return <Empty title="暂无整理计划" description="完成一次扫描后，系统会先生成文件整理预览。" cta={{ label: '开始扫描', to: '/scan' }} />
  const selectedStatus = id === null ? undefined : list.data.items.find(plan => Number(plan.id) === id)?.status
  return <><div className="plan-tabs-row"><div className="plan-tabs">{list.data.items.map(plan => <button key={plan.id} className={id === plan.id ? 'active' : ''} onClick={() => { setManualSelection(true); setId(plan.id) }}>#{plan.id} <Status value={plan.status} /></button>)}</div>{selectedStatus && dismissible(selectedStatus) && id !== null ? <button className="text-button danger" onClick={() => { if (window.confirm(`忽略计划 #${id}？该计划已过期/结束，其未审核项将一并清除。`)) dismiss.mutate(id) }}><Trash2 size={13} />忽略</button> : null}</div>{planError ? <div className="form-error">{planError}</div> : null}{detail.data ? <PlanWorkspace plan={detail.data} onApprove={() => approve.mutate()} onApproveApproved={() => approveApproved.mutate()} onApproveItem={itemId => approveItem.mutate(itemId)} onRejectItem={(itemId, reason) => rejectItem.mutate({ itemId, reason })} /> : <Empty>正在载入计划…</Empty>}</>
}

export function InboxPage() {
  const [params, setParams] = useSearchParams(); const tab = params.get('tab') === 'plans' ? 'plans' : 'reviews'; const reviews = useList('reviews', '/reviews', 5000); const plans = useList('plans', '/plans', 5000); const [success, setSuccess] = useState(false)
  const switchTab = (next: string) => setParams({ tab: next })
  return <Page title="待处理" description="确认识别结果，并在执行前检查整理计划。"><div className="tab-list inbox-tabs" role="tablist"><button className={`tab-button${tab === 'reviews' ? ' active' : ''}`} onClick={() => switchTab('reviews')}>需要确认{reviews.data ? ` (${reviews.data.items.length})` : ''}</button><button className={`tab-button${tab === 'plans' ? ' active' : ''}`} onClick={() => switchTab('plans')}>整理计划{plans.data ? ` (${plans.data.items.length})` : ''}</button></div>{success ? <div className="success-note">信息已确认，已生成新的整理计划</div> : null}{tab === 'reviews' ? <ReviewsPanel active onResolved={() => { setSuccess(true); switchTab('plans') }} /> : <PlansPanel active />}</Page>
}
export function ReviewsPage() { return <InboxPage /> }
export function PlansPage() { return <InboxPage /> }
export function LibraryPage() {
  const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null); const [title, setTitle] = useState(''); const [reason, setReason] = useState(''); const [locked, setLocked] = useState(true); const [preview, setPreview] = useState<Item | null>(null); const [libError, setLibError] = useState('')
  const [q, setQ] = useState(''); const [debouncedQ, setDebouncedQ] = useState(''); const [sort, setSort] = useState('title')
  useEffect(() => { const timer = setTimeout(() => setDebouncedQ(q.trim()), 250); return () => clearTimeout(timer) }, [q])
  const query = useQuery({ queryKey: ['shows', debouncedQ, sort], queryFn: () => api.get<ListResponse>(`/library/shows?q=${encodeURIComponent(debouncedQ)}&sort=${sort}`) })
  const detail = useQuery({ queryKey: ['show', selected?.id], queryFn: () => api.get<Item>(`/library/shows/${selected?.id}`), enabled: Boolean(selected) })
  const impact = useQuery({ queryKey: ['change-impact', selected?.id, title], queryFn: () => api.post<Item>('/library/changes/impact', { show_id: selected?.id, base_revision: selected?.revision, patch: { canonical_title: title }, reason: '' }), enabled: Boolean(selected && title && title.trim() && title !== selected?.canonical_title) })
  const previewChange = useMutation({ mutationFn: () => api.post<Item>('/library/changes/preview', { show_id: selected?.id, base_revision: selected?.revision, patch: { canonical_title: title, title_locked: locked }, reason }), onSuccess: setPreview, onError: reason => setLibError(reason instanceof Error ? reason.message : '预览修改失败') })
  const approve = useMutation({ mutationFn: () => api.post<Item>(`/library/changes/${preview?.id}/approve`), onSuccess: async updated => { const showId = Number(updated?.id ?? selected?.id); setPreview(null); setTitle(''); setReason(''); await client.invalidateQueries({ queryKey: ['shows'] }); if (showId) { await client.invalidateQueries({ queryKey: ['show', showId] }); const refreshed = await api.get<Item>(`/library/shows/${showId}`).catch(() => updated); setSelected(refreshed) } }, onError: reason => setLibError(reason instanceof Error ? reason.message : '批准修改失败') })
  const metadata = detail.data?.metadata?.[0]
  const moving = Number(impact.data?.files_to_move ?? 0); const discarding = Number(impact.data?.files_to_discard ?? 0)
  const fileBase = (path: string) => path.split(/[\\/]/).pop() || path
  const recentWithin = (value: unknown, days: number) => { const time = new Date(String(value ?? '')).getTime(); return Number.isFinite(time) && Date.now() - time < days * 86400000 }
  const shortDate = (value: unknown) => { const time = new Date(String(value ?? '')).getTime(); if (!Number.isFinite(time)) return '—'; const d = new Date(time); const pad = (n: number) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` }
  const episodeLabel = (ep: Item) => ep.episode_type === 'movie' ? '电影' : ep.episode_type === 'special' ? (String(ep.episode_number).startsWith('SP') ? String(ep.episode_number) : `SP${episodeToken(String(ep.episode_number ?? '0'))}`) : `E${episodeToken(String(ep.episode_number ?? '0'))}`
  return <Page title="资料库" description="查看番剧、季集、文件位置、识别证据和附加元数据，搜索并纠正整理结果">
    {query.error ? <Empty title="无法读取资料库" description={queryErrorMessage(query.error, '请稍后重试')} /> : !query.data?.items.length && !debouncedQ ? <Empty title="资料库还是空的" description="批准并完成一次整理后，番剧会显示在这里。" cta={{ label: '配置目录', to: '/scan' }} /> : <div className="plan-layout"><section className="surface">
      <div className="surface-title"><h2>番剧</h2><span>{query.data?.items.length ?? 0} 部</span></div>
      <div className="library-toolbar"><input className="search-input" aria-label="搜索番剧" placeholder="搜索番剧名称…" value={q} onChange={event => setQ(event.target.value)} /><div className="chip-row" role="group" aria-label="排序方式"><button type="button" className={`chip${sort === 'title' ? ' active' : ''}`} onClick={() => setSort('title')}>按标题</button><button type="button" className={`chip${sort === 'recent' ? ' active' : ''}`} onClick={() => setSort('recent')}>最近更新</button></div></div>
      {query.data?.items.length ? <div className="library-list">{query.data.items.map(item => <button type="button" key={item.id} className={`library-row${selected?.id === item.id ? ' active' : ''}`} onClick={() => { setSelected(item); setTitle(item.canonical_title); setPreview(null) }}><span className="library-row-title"><strong>{item.canonical_title}</strong>{recentWithin(item.created_at, 14) ? <em className="badge-new">新番</em> : null}{recentWithin(item.recent_activity, 14) ? <em className="badge-updated">更新</em> : null}</span><span className="library-row-meta">{item.season_count ?? 0} 季 · {item.episode_count ?? 0} 集</span><span className="library-row-time">{shortDate(item.recent_activity)}</span></button>)}</div> : <Empty title="没有匹配的番剧" description="换个关键词再试试。" />}
    </section><aside className="inspector">{selected ? <>{metadata?.poster_url ? <img className="poster" src={metadata.poster_url} alt={`${selected.canonical_title} 海报`} /> : null}<h2>{selected.canonical_title}</h2><p className="muted-copy">{metadata?.synopsis || '暂无简介；元数据不可用不会影响文件整理。'}</p><dl><dt>放送状态</dt><dd>{metadata?.broadcast_status || '未知'}</dd><dt>季数</dt><dd>{detail.data?.seasons?.length ?? 0}</dd><dt>修订</dt><dd>{selected.revision}</dd><dt>最近更新</dt><dd>{shortDate(selected.recent_activity)}</dd></dl><div className="season-blocks">{detail.data?.seasons?.map((season: Item) => <div className="season-block" key={season.id}><div className="season-head"><strong>{season.season_number === 0 ? '特别篇' : `Season ${String(season.season_number).padStart(2, '0')}`}</strong><span>{season.episodes?.length ?? 0} 集</span></div>{season.episodes?.length ? <div className="episode-list">{season.episodes.map((ep: Item) => { const files = Array.isArray(ep.files) && ep.files.length ? ep.files : [null]; return files.map((file: Item | null, index: number) => <div className="episode-item" key={`${ep.id}-${index}`}><span className="ep-token">{index === 0 ? episodeLabel(ep) : ''}</span><span className="ep-file" title={file?.path || ''}>{file?.path ? fileBase(file.path) : (ep.display_title || '—')}</span>{file?.release_label ? <span className="ep-tag">{file.release_label}</span> : null}</div>) })}</div> : <p className="muted">暂无剧集</p>}</div>)}</div><label>新规范标题<input aria-label="新规范标题" value={title} onChange={event => setTitle(event.target.value)} /></label><label>修改原因<input aria-label="修改原因" value={reason} onChange={event => setReason(event.target.value)} /></label><label className="check-field"><input type="checkbox" checked={locked} onChange={event => setLocked(event.target.checked)} />锁定人工标题</label>{libError ? <div className="form-error">{libError}</div> : null}{impact.data?.merge ? <div className="risk-warning">该标题已是番剧「{impact.data.target_show?.canonical_title}」。批准后将合并：移动 {moving} 个文件，同集自动留大删小（{discarding} 个）。</div> : moving + discarding > 0 ? <div className="risk-warning">批准后将移动 {moving} 个文件{discarding > 0 ? `，并删小留大 ${discarding} 个` : ''}到新文件夹。</div> : null}{preview ? <div className="change-preview"><strong>修改预览</strong><span>{String(preview.old_values?.canonical_title)} → {String(preview.new_values?.canonical_title)}</span><button className="primary full" onClick={() => approve.mutate()}><ShieldCheck size={16} />批准修改</button></div> : <button className="secondary full" disabled={!title || !reason || title === selected.canonical_title} onClick={() => previewChange.mutate()}><Check size={16} />预览修改</button>}<ReviewChat kind="library" targetId={Number(selected.id)} onApplied={async () => { const showId = Number(selected.id); await client.invalidateQueries({ queryKey: ['shows'] }); await client.invalidateQueries({ queryKey: ['show', showId] }); const refreshed = await api.get<Item>(`/library/shows/${showId}`).catch(() => selected); setSelected(refreshed); setTitle(String(refreshed.canonical_title || title)); setPreview(null) }} /></> : <Empty>选择番剧查看季集详情和纠正</Empty>}</aside></div>}
  </Page>
}

function RulesPanel({ active = true }: { active?: boolean }) {
  const query = useList('rules', '/rules', 0, active); const client = useQueryClient(); const [name, setName] = useState(''); const [selectedId, setSelectedId] = useState<number | null>(null); const [document, setDocument] = useState('{\n  "aliases": {}\n}'); const [error, setError] = useState('')
  const selected = query.data?.items.find(item => item.id === selectedId) || query.data?.items[0]
  useEffect(() => { if (selectedId === null && query.data?.items[0]?.id) setSelectedId(Number(query.data.items[0].id)) }, [query.data?.items, selectedId])
  const refresh = () => client.invalidateQueries({ queryKey: ['rules'] })
  const createSet = useMutation({ mutationFn: () => api.post<Item>('/rules', { name }), onSuccess: async item => { setName(''); setSelectedId(item.id); await refresh() }, onError: reason => setError(reason instanceof Error ? reason.message : '创建规则集失败') })
  const createRevision = useMutation({ mutationFn: async () => { setError(''); let parsed: Item; try { parsed = JSON.parse(document) } catch { throw new Error('规则 JSON 格式无效') } return api.post('/rules/revisions', { rule_set_id: selected?.id, document: parsed }) }, onSuccess: refresh, onError: reason => setError(reason instanceof Error ? reason.message : '保存失败') })
  const validate = useMutation({ mutationFn: (id: number) => api.post(`/rules/revisions/${id}/validate`), onSuccess: refresh, onError: reason => setError(reason instanceof Error ? reason.message : '校验失败') })
  const activate = useMutation({ mutationFn: (id: number) => api.post(`/rules/revisions/${id}/activate`), onSuccess: refresh, onError: reason => setError(reason instanceof Error ? reason.message : '激活失败') })
  const rollback = useMutation({ mutationFn: (id: number) => api.post(`/rules/${selected?.id}/revisions/${id}/rollback`), onSuccess: refresh, onError: reason => setError(reason instanceof Error ? reason.message : '回退失败') })
  const latest = selected?.revisions?.[0]
  return <><p className="risk-warning">仅供熟悉 AutoAnime 规则格式的用户使用。普通用户无需创建或修改规则 JSON。</p><div className="plan-layout"><section className="surface"><form className="inline-form" onSubmit={event => { event.preventDefault(); createSet.mutate() }}><input aria-label="规则集名称" value={name} onChange={event => setName(event.target.value)} placeholder="例如：默认别名" /><button className="primary" disabled={!name}><Plus size={16} />新建规则集</button></form><DataTable items={query.data?.items || []} columns={['id', 'name', 'active_revision_id', 'updated_at']} onRow={item => setSelectedId(item.id)} selectedId={selected?.id} /></section><aside className="inspector">{selected ? <><h2>{selected.name}</h2><label>规则 JSON<textarea aria-label="规则 JSON" value={document} onChange={event => setDocument(event.target.value)} /></label>{error ? <div className="form-error">{error}</div> : null}<button className="secondary full" onClick={() => createRevision.mutate()}><Save size={16} />保存草稿</button><div className="revision-list">{selected.revisions?.map((revision: Item) => <div key={revision.id}><strong>rev {revision.revision}</strong><Status value={revision.status} /><div className="row-actions">{revision.status === 'draft' ? <button className="text-button" onClick={() => validate.mutate(revision.id)}>校验</button> : null}{revision.status === 'validated' ? <button className="text-button" onClick={() => activate.mutate(revision.id)}>激活</button> : null}{revision.content_hash && revision.status !== 'active' ? <button className="text-button" onClick={() => rollback.mutate(revision.id)}>回退到此版</button> : null}</div></div>)}</div>{latest ? <pre className="evidence">{JSON.stringify(latest.document, null, 2)}</pre> : <Empty>尚无修订</Empty>}</> : <Empty>先创建规则集</Empty>}</aside></div></>
}
export function RulesPage() { return <Page title="规则与别名"><RulesPanel /></Page> }

function OperationsPanel({ active = true }: { active?: boolean }) {
  const query = useList('operations', '/operations', 5000, active); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null); const [opError, setOpError] = useState(''); const detail = useQuery({ queryKey: ['operation', selected?.id], queryFn: () => api.get<Item>(`/operations/${selected?.id}`), enabled: active && Boolean(selected) })
  const rollback = useMutation({ mutationFn: (id: number) => api.post(`/operations/${id}/rollback`), onSuccess: async () => { await client.invalidateQueries({ queryKey: ['operations'] }); await detail.refetch() }, onError: reason => setOpError(reason instanceof Error ? reason.message : '回滚失败') })
  const requestRollback = (id: number) => { if (window.confirm('将尝试撤销本批次的文件操作，是否继续？')) rollback.mutate(id) }
  if (query.error) return <Empty title="无法读取整理记录" description={queryErrorMessage(query.error, '请稍后重试')} />
  if (!query.data?.items.length) return <Empty title="还没有整理记录" description="批准并执行一次整理计划后，文件操作会显示在这里。" />
  return <div className="plan-layout">{opError ? <div className="form-error">{opError}</div> : null}<section className="surface"><DataTable items={query.data.items} columns={['id', 'kind', 'status', 'created_at', 'finished_at']} onRow={setSelected} selectedId={selected?.id} action={item => ['execute', 'correction'].includes(item.kind) && item.status === 'completed' ? <button className="text-button danger" onClick={() => requestRollback(item.id)}><RotateCcw size={14} />回滚</button> : null} /></section><aside className="inspector">{detail.data ? <><h2>批次 #{detail.data.id}</h2><Status value={detail.data.status} /><pre className="evidence">{JSON.stringify(detail.data.summary, null, 2)}</pre><div className="event-list">{detail.data.items?.map((item: Item) => <div key={item.id}><strong>{item.action} · {item.status}</strong><span>{item.source_path} → {item.destination_path}</span></div>)}</div></> : <Empty>选择批次查看文件摘要</Empty>}</aside></div>
}
export function OperationsPage() { return <Page title="整理记录"><OperationsPanel /></Page> }
export function ActivityPage() {
  const [params, setParams] = useSearchParams(); const tab = params.get('tab') === 'operations' ? 'operations' : 'jobs'
  return <Page title="运行记录" description="查看扫描进度、任务事件和已完成的文件操作。"><div className="tab-list activity-tabs"><button className={`tab-button${tab === 'jobs' ? ' active' : ''}`} onClick={() => setParams({ tab: 'jobs' })}>任务进度</button><button className={`tab-button${tab === 'operations' ? ' active' : ''}`} onClick={() => setParams({ tab: 'operations' })}>整理记录</button></div>{tab === 'jobs' ? <JobsPanel active /> : <OperationsPanel active />}</Page>
}
export function AutomationSettings({ profiles, schedules, webhooks, createdToken, onCreateSchedule, onToggleSchedule, onDeleteSchedule, onCreateWebhook, onToggleWebhook, onDeleteWebhook }: {
  profiles: Item[]
  schedules: Item[]
  webhooks: Item[]
  createdToken: string
  onCreateSchedule: (value: Item) => void
  onToggleSchedule: (value: Item) => void
  onDeleteSchedule?: (value: Item) => void
  onCreateWebhook: (value: Item) => void
  onToggleWebhook: (value: Item) => void
  onDeleteWebhook?: (value: Item) => void
}) {
  const [scheduleProfile, setScheduleProfile] = useState(String(profiles[0]?.id || ''))
  const [scheduleKind, setScheduleKind] = useState<'interval' | 'daily'>('interval')
  const [intervalMinutes, setIntervalMinutes] = useState(15)
  const [dailyTime, setDailyTime] = useState('09:00')
  const [webhookProfile, setWebhookProfile] = useState(String(profiles[0]?.id || ''))
  const [webhookName, setWebhookName] = useState('qBittorrent')
  useEffect(() => {
    if (!scheduleProfile && profiles[0]) setScheduleProfile(String(profiles[0].id))
    if (!webhookProfile && profiles[0]) setWebhookProfile(String(profiles[0].id))
  }, [profiles, scheduleProfile, webhookProfile])
  const profileName = (id: number) => profiles.find(profile => Number(profile.id) === Number(id))?.name || `#${id}`
  const presets = [5, 15, 30, 60, 120]
  const createSchedule = () => {
    if (scheduleKind === 'daily') onCreateSchedule({ profile_id: Number(scheduleProfile), kind: 'daily', schedule: { time: dailyTime }, timezone: 'Asia/Shanghai' })
    else onCreateSchedule({ profile_id: Number(scheduleProfile), kind: 'interval', schedule: { interval_minutes: intervalMinutes }, timezone: 'UTC' })
  }
  return <section className="surface form-surface automation-settings">
    <div className="surface-title"><h2>自动扫描</h2><span>默认用 qBittorrent 完成通知；定时扫描为备选</span></div>
    <p className="muted form-indent">推荐先创建 Webhook：下载完成后自动扫描该方案绑定的下载源，并硬链接到对应媒体库。每个方案各绑一对目录；多源进同一库请建多个方案。创建 Webhook 时会把该方案改为「安全项自动 + 硬链接」。</p>
    {profiles.length === 0 ? <p className="muted form-indent">尚无扫描方案，请先在「目录与扫描」创建扫描方案后再添加定时计划或 Webhook。</p> : null}
    <div className="automation-grid">
      <div className="automation-card">
        <div className="automation-card-head"><h3>定时计划</h3><span>按间隔或每天自动扫描</span></div>
        <div className="automation-fields">
          <label>扫描配置<select aria-label="计划扫描配置" value={scheduleProfile} onChange={event => setScheduleProfile(event.target.value)}>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
          <label>计划类型<select aria-label="计划类型" value={scheduleKind} onChange={event => setScheduleKind(event.target.value as 'interval' | 'daily')}><option value="interval">固定间隔</option><option value="daily">每天指定时间</option></select></label>
          {scheduleKind === 'interval' ? <label>扫描间隔
            <div className="chip-row" role="group" aria-label="间隔预设">{presets.map(minutes => <button type="button" key={minutes} className={intervalMinutes === minutes ? 'chip active' : 'chip'} onClick={() => setIntervalMinutes(minutes)}>{minutes < 60 ? `${minutes} 分钟` : `${minutes / 60} 小时`}</button>)}</div>
            <input aria-label="间隔分钟" type="number" min={1} value={intervalMinutes} onChange={event => setIntervalMinutes(Number(event.target.value))} />
          </label> : <label>每天时间<input aria-label="每天时间" type="time" value={dailyTime} onChange={event => setDailyTime(event.target.value)} /></label>}
          <div className="form-actions"><button className="secondary" disabled={!scheduleProfile || (scheduleKind === 'interval' ? intervalMinutes < 1 : !dailyTime)} onClick={createSchedule}><Plus size={16} />创建计划</button></div>
        </div>
        <div className="automation-list">{schedules.length ? schedules.map(schedule => <div className="automation-item" key={schedule.id}><div className="automation-item-main"><strong>{schedule.kind === 'interval' ? `每 ${schedule.schedule.interval_minutes} 分钟` : `每天 ${schedule.schedule.time}`}</strong><span>{profileName(schedule.profile_id)} · 下次 {schedule.next_run_at || '已停用'}</span></div><div className="row-actions"><button className="text-button" aria-label={schedule.enabled ? '停用计划' : '启用计划'} onClick={() => onToggleSchedule(schedule)}>{schedule.enabled ? '停用' : '启用'}</button>{onDeleteSchedule ? <button className="text-button danger" aria-label="删除计划" onClick={() => { if (window.confirm('删除这条定时计划？')) onDeleteSchedule(schedule) }}><Trash2 size={13} />删除</button> : null}</div></div>) : <Empty>还没有定时计划</Empty>}</div>
      </div>
      <div className="automation-card">
        <div className="automation-card-head"><h3>下载器 Webhook</h3><span>下载完成后回调触发扫描</span></div>
        <div className="automation-fields">
          <label>名称<input aria-label="Webhook 名称" value={webhookName} onChange={event => setWebhookName(event.target.value)} /></label>
          <label>绑定配置<select aria-label="Webhook 扫描配置" value={webhookProfile} onChange={event => setWebhookProfile(event.target.value)}>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
          <div className="form-actions"><button className="secondary" disabled={!webhookName || !webhookProfile} onClick={() => onCreateWebhook({ name: webhookName, downloader: 'qbittorrent', profile_id: Number(webhookProfile) })}><Plus size={16} />创建 Webhook</button></div>
        </div>
        {createdToken ? <QbWebhookWizard token={createdToken} /> : null}
        <div className="automation-list">{webhooks.length ? webhooks.map(webhook => <div className="automation-item" key={webhook.id}><div className="automation-item-main"><strong>{webhook.name}</strong><span>{profileName(webhook.profile_id)} · 最后调用 {webhook.last_called_at || '尚未调用'}</span></div><div className="row-actions"><button className="text-button" aria-label={webhook.enabled ? '停用 Webhook' : '启用 Webhook'} onClick={() => onToggleWebhook(webhook)}>{webhook.enabled ? '停用' : '启用'}</button>{onDeleteWebhook ? <button className="text-button danger" aria-label="删除 Webhook" onClick={() => { if (window.confirm(`删除 Webhook「${webhook.name}」？`)) onDeleteWebhook(webhook) }}><Trash2 size={13} />删除</button> : null}</div></div>) : <Empty>还没有 Webhook</Empty>}</div>
      </div>
    </div>
  </section>
}

function MemoryTable() {
  const query = useQuery({ queryKey: ['memory'], queryFn: () => api.get<ListResponse>('/memory') })
  return <section className="surface form-surface">
    <div className="surface-title"><h2>已记住的作品名</h2><span>{query.data?.items.length ?? 0} 条</span></div>
    <p className="muted form-indent">识别确认和资料库纠正会记住别名，下次扫描直接套用。不会改规则版本。</p>
    <DataTable items={query.data?.items || []} columns={['alias_key', 'canonical_title', 'source', 'confidence', 'updated_at']} />
  </section>
}

export function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const requestedTab = params.get('tab')
  const tab = requestedTab === 'automation' || requestedTab === 'advanced' ? requestedTab : 'general'
  const panel = params.get('panel') === 'raw' ? 'raw' : 'rules'
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<{ items: Item[]; secrets: Item[]; security?: Item; openai?: Item; metadata?: Item }>('/settings'),
    enabled: tab === 'general' || (tab === 'advanced' && panel === 'raw'),
  })
  const backups = useList('backups', '/backups', 0, tab === 'advanced')
  const schedules = useList('schedules', '/schedules', 0, tab === 'automation')
  const webhooks = useList('webhook-sources', '/webhook-sources', 0, tab === 'automation')
  const profiles = useList('automation-profiles', '/profiles', 0, tab === 'automation')
  const client = useQueryClient()
  const [openaiKey, setOpenaiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [timeout, setTimeoutSeconds] = useState('30')
  const [aiError, setAiError] = useState('')
  const [aiMessage, setAiMessage] = useState('')
  const [settingsError, setSettingsError] = useState('')
  const [createdToken, setCreatedToken] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const security = query.data?.security || {}
  const openai = query.data?.openai || {}

  useEffect(() => {
    if (!query.data?.openai) return
    setBaseUrl(String(query.data.openai.base_url || 'https://api.openai.com'))
    setModel(String(query.data.openai.model || 'gpt-4.1-mini'))
    setTimeoutSeconds(String(query.data.openai.timeout ?? 30))
  }, [query.data?.openai])

  const saveSetting = useMutation({
    mutationFn: ({ key, value, revision }: { key: string; value: unknown; revision: number }) =>
      api.patch('/settings', { key, value, revision }),
    onSuccess: async () => {
      setSettingsError('')
      await client.invalidateQueries({ queryKey: ['settings'] })
      await client.invalidateQueries({ queryKey: ['bootstrap-status'] })
    },
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '保存设置失败'),
  })
  // AI 参与方式：单个下拉框覆盖 AI 是否启用、划分范围、是否启用复核代理。
  const AI_MODES: Record<string, { enabled: boolean; review: boolean; mode: string }> = {
    off: { enabled: false, review: false, mode: 'off' },
    uncertain: { enabled: true, review: false, mode: 'uncertain' },
    all: { enabled: true, review: false, mode: 'all' },
    'uncertain+review': { enabled: true, review: true, mode: 'uncertain' },
    'all+review': { enabled: true, review: true, mode: 'all' },
  }
  const aiMode =
    !openai.enabled || String(openai.parse_agent_mode || 'off') === 'off'
      ? 'off'
      : openai.review_enabled
        ? String(openai.parse_agent_mode || '') === 'all'
          ? 'all+review'
          : 'uncertain+review'
        : String(openai.parse_agent_mode || '') === 'all'
          ? 'all'
          : 'uncertain'
  const onAiModeChange = async (mode: string) => {
    const target = AI_MODES[mode]
    if (!target) return
    try {
      const apply = async (key: string, value: unknown, revision: number, current: unknown) => {
        if (current === value) return
        await api.patch('/settings', { key, value, revision })
      }
      let latest = await api.get<{ openai?: Item }>('/settings')
      let current = latest.openai || {}
      await apply('openai.enabled', target.enabled, Number(current.enabled_revision || 0), Boolean(current.enabled))
      latest = await api.get<{ openai?: Item }>('/settings')
      current = latest.openai || {}
      await apply('review.enabled', target.review, Number(current.review_enabled_revision || 0), Boolean(current.review_enabled))
      latest = await api.get<{ openai?: Item }>('/settings')
      current = latest.openai || {}
      await apply('parse.agent_mode', target.mode, Number(current.parse_agent_mode_revision || 0), String(current.parse_agent_mode || 'off'))
      await client.invalidateQueries({ queryKey: ['settings'] })
    } catch (reason) {
      setSettingsError(reason instanceof Error ? reason.message : '保存设置失败')
    }
  }
  const saveOpenaiConfig = useMutation({
    mutationFn: async () => {
      setAiError('')
      setAiMessage('')
      // Refresh revisions before multi-patch to reduce conflicts.
      const latest = await api.get<{ openai?: Item }>('/settings')
      const current = latest.openai || {}
      await api.patch('/settings', {
        key: 'openai.base_url',
        value: baseUrl.trim(),
        revision: Number(current.base_url_revision || 0),
      })
      const afterBase = await api.get<{ openai?: Item }>('/settings')
      const mid = afterBase.openai || {}
      await api.patch('/settings', {
        key: 'openai.model',
        value: model.trim(),
        revision: Number(mid.model_revision || 0),
      })
      const afterModel = await api.get<{ openai?: Item }>('/settings')
      const last = afterModel.openai || {}
      await api.patch('/settings', {
        key: 'openai.timeout',
        value: Number(timeout),
        revision: Number(last.timeout_revision || 0),
      })
      if (openaiKey.trim()) {
        await api.put('/settings/secrets/openai.api_key', { value: openaiKey.trim() })
        setOpenaiKey('')
      }
    },
    onSuccess: async () => {
      setAiMessage('AI 识别设置已保存')
      await client.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: reason => setAiError(reason instanceof Error ? reason.message : '保存失败'),
  })
  const [metadataKey, setMetadataKey] = useState('')
  const [metadataTimeout, setMetadataTimeout] = useState('12')
  const [metaError, setMetaError] = useState('')
  const [metaMessage, setMetaMessage] = useState('')
  const metadata = query.data?.metadata || {}

  useEffect(() => {
    if (!query.data?.metadata) return
    setMetadataTimeout(String(query.data.metadata.timeout ?? 12))
  }, [query.data?.metadata])

  const saveMetadataConfig = useMutation({
    mutationFn: async () => {
      setMetaError('')
      setMetaMessage('')
      const latest = await api.get<{ metadata?: Item }>('/settings')
      const current = latest.metadata || {}
      await api.patch('/settings', {
        key: 'metadata.timeout',
        value: Number(metadataTimeout),
        revision: Number(current.timeout_revision || 0),
      })
      if (metadataKey.trim()) {
        await api.put('/settings/secrets/metadata.tmdb_api_key', { value: metadataKey.trim() })
        setMetadataKey('')
      }
    },
    onSuccess: async () => {
      setMetaMessage('元数据设置已保存')
      await client.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: reason => setMetaError(reason instanceof Error ? reason.message : '保存失败'),
  })
  const backup = useMutation({
    mutationFn: () => api.post('/backups'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['backups'] }),
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '备份失败'),
  })
  const createSchedule = useMutation({
    mutationFn: async (value: Item) => {
      const created = await api.post('/schedules', value)
      const profile = (profiles.data?.items || []).find(item => Number(item.id) === Number(value.profile_id))
      if (profile) {
        await api.patch(`/profiles/${profile.id}`, {
          revision: profile.revision,
          patch: { execution_policy: 'auto_apply_safe', mode: 'link' },
        })
      }
      return created
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['schedules'] })
      await client.invalidateQueries({ queryKey: ['automation-profiles'] })
      await client.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '创建计划失败'),
  })
  const toggleSchedule = useMutation({
    mutationFn: (value: Item) =>
      api.patch(`/schedules/${value.id}`, {
        revision: value.revision,
        patch: { enabled: !Boolean(value.enabled) },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['schedules'] }),
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '切换计划失败'),
  })
  const createWebhook = useMutation({
    mutationFn: async (value: Item) => {
      const created = await api.post<Item>('/webhook-sources', value)
      const profile = (profiles.data?.items || []).find(item => Number(item.id) === Number(value.profile_id))
      if (profile) {
        await api.patch(`/profiles/${profile.id}`, {
          revision: profile.revision,
          patch: { execution_policy: 'auto_apply_safe', mode: 'link' },
        })
      }
      return created
    },
    onSuccess: async value => {
      setCreatedToken(String(value.token || ''))
      await client.invalidateQueries({ queryKey: ['webhook-sources'] })
      await client.invalidateQueries({ queryKey: ['automation-profiles'] })
      await client.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '创建 Webhook 失败'),
  })
  const toggleWebhook = useMutation({
    mutationFn: (value: Item) =>
      api.patch(`/webhook-sources/${value.id}`, {
        revision: value.revision,
        patch: { enabled: !Boolean(value.enabled) },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['webhook-sources'] }),
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '切换 Webhook 失败'),
  })
  const deleteSchedule = useMutation({
    mutationFn: (value: Item) => api.delete(`/schedules/${value.id}`, { revision: value.revision }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['schedules'] }),
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '删除计划失败'),
  })
  const deleteWebhook = useMutation({
    mutationFn: (value: Item) => api.delete(`/webhook-sources/${value.id}`, { revision: value.revision }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['webhook-sources'] }),
    onError: reason => setSettingsError(reason instanceof Error ? reason.message : '删除 Webhook 失败'),
  })

  return (
    <Page title="设置" description="管理常用功能、自动扫描和高级维护。">
      <div className="tab-list settings-tabs"><button className={`tab-button${tab === 'general' ? ' active' : ''}`} onClick={() => setParams({ tab: 'general' })}>常用</button><button className={`tab-button${tab === 'automation' ? ' active' : ''}`} onClick={() => setParams({ tab: 'automation' })}>自动化</button><button className={`tab-button${tab === 'advanced' ? ' active' : ''}`} onClick={() => setParams({ tab: 'advanced', panel: 'rules' })}>高级</button></div>
      {settingsError ? <div className="form-error">{settingsError}</div> : null}
      {tab === 'general' ? <><section className="surface form-surface">
        <div className="surface-title">
          <h2>AI 识别（可选）</h2>
          <span>
            {openai.ready
              ? '已启用且已配置 Key'
              : openai.enabled
                ? '已开启，但仍需保存 API Key'
                : '默认关闭 · 仅处理本地无法收敛的文件'}
          </span>
        </div>
        <label>
          AI 参与方式
          <select aria-label="AI 参与方式" value={aiMode} onChange={event => onAiModeChange(event.target.value)}>
            <option value="off">关闭 AI（使用本地机器规则）</option>
            <option value="uncertain">AI 划分：仅低置信度文件</option>
            <option value="all">AI 划分：所有文件</option>
            <option value="uncertain+review">AI 划分（仅低置信度文件）+ 复核代理</option>
            <option value="all+review">AI 划分（所有文件）+ 复核代理</option>
          </select>
        </label>
        <p className="muted form-indent">
          关闭时只用本地规则与 Bangumi/TMDB 检索。开启后按文件夹（平铺目录则按作品聚类）一次识别整组文件，
          高置信结果会记住作品名供下次扫描复用。拿不准或混了多部作品时仍会进入「待处理」人工确认，整理路径仍由计划预览/执行产生。
        </p>
        <div className="settings-grid">
          <label>
            API Base URL
            <input
              aria-label="API Base URL"
              value={baseUrl}
              onChange={event => setBaseUrl(event.target.value)}
              placeholder="https://api.openai.com 或中转地址"
            />
          </label>
          <label>
            模型
            <input
              aria-label="模型"
              value={model}
              onChange={event => setModel(event.target.value)}
              placeholder="gpt-4.1-mini"
            />
          </label>
          <label>
            超时秒数
            <input
              aria-label="超时秒数"
              type="number"
              min={5}
              value={timeout}
              onChange={event => setTimeoutSeconds(event.target.value)}
            />
          </label>
          <label>
            API Key
            <input
              aria-label="API Key"
              type="password"
              value={openaiKey}
              onChange={event => setOpenaiKey(event.target.value)}
              placeholder={openai.api_key_configured ? '已配置 · 输入新值可替换' : 'sk-...（加密保存）'}
            />
          </label>
        </div>
        {aiError ? <div className="form-error form-indent">{aiError}</div> : null}
        {aiMessage ? <p className="muted form-indent">{aiMessage}</p> : null}
        <div className="form-actions form-indent">
          <button
            className="primary"
            onClick={() => saveOpenaiConfig.mutate()}
            disabled={saveOpenaiConfig.isPending || !baseUrl.trim() || !model.trim()}
          >
            <Save size={16} />
            保存 AI 设置
          </button>
        </div>
        <p className="muted form-indent">
          支持 OpenAI 官方与兼容中转。Base URL 可填主机根或带 `/v1`；系统会自动补全 chat completions 路径。
          Key 仅加密保存在本机，不会回显、不会提交到 Git，也不会上传到本项目作者或任何“官方云”。
          仅在开启且本地无法识别时，才向你填写的 Base URL 发送文件名等文本（不发送视频文件）。不需要时请保持关闭。
        </p>
      </section>
      <MemoryTable />

      <section className="surface form-surface">
        <div className="surface-title">
          <h2>番剧元数据（可选）</h2>
          <span>
            {metadata.ready
              ? '已启用'
              : metadata.bangumi_enabled || metadata.tmdb_enabled
                ? '已开启，但尚未完全可用'
                : '默认关闭 · 用于查找中文名、海报与简介'}
          </span>
        </div>
        <label className="check-field">
          <input
            type="checkbox"
            aria-label="使用 Bangumi"
            disabled={!query.data || saveSetting.isPending}
            checked={Boolean(metadata.bangumi_enabled)}
            onChange={event =>
              saveSetting.mutate({
                key: 'metadata.bangumi_enabled',
                value: event.target.checked,
                revision: Number(metadata.bangumi_enabled_revision || 0),
              })
            }
          />
          启用 Bangumi（bgm.tv）匹配中文名（无需 API Key）
        </label>
        <label className="check-field">
          <input
            type="checkbox"
            aria-label="使用 TMDB"
            disabled={!query.data || saveSetting.isPending}
            checked={Boolean(metadata.tmdb_enabled)}
            onChange={event =>
              saveSetting.mutate({
                key: 'metadata.tmdb_enabled',
                value: event.target.checked,
                revision: Number(metadata.tmdb_enabled_revision || 0),
              })
            }
          />
          启用 TMDB 匹配中文名（需要下面的 API Key）
        </label>
        <div className="settings-grid">
          <label>
            超时秒数
            <input
              aria-label="元数据超时秒数"
              type="number"
              min={2}
              value={metadataTimeout}
              onChange={event => setMetadataTimeout(event.target.value)}
            />
          </label>
          <label>
            TMDB API Key
            <input
              aria-label="TMDB API Key"
              type="password"
              value={metadataKey}
              onChange={event => setMetadataKey(event.target.value)}
              placeholder={metadata.tmdb_api_key_configured ? '已配置 · 输入新值可替换' : 'tmdb...（加密保存）'}
            />
          </label>
        </div>
        {metaError ? <div className="form-error form-indent">{metaError}</div> : null}
        {metaMessage ? <p className="muted form-indent">{metaMessage}</p> : null}
        <div className="form-actions form-indent">
          <button className="primary" onClick={() => saveMetadataConfig.mutate()} disabled={saveMetadataConfig.isPending}>
            <Save size={16} />
            保存元数据设置
          </button>
        </div>
        <p className="muted form-indent">
          本地无法确定中文名时，会按顺序查询 Bangumi → TMDB 补齐中文名，并在资料库中显示匹配到的海报、简介与放送状态。
          TMDB Key 仅加密保存在本机，不会回显。两个来源都关闭时完全不起作用。
        </p>
      </section>

      <section className="surface form-surface">
        <div className="surface-title">
          <h2>本机访问与 Hook</h2>
          <span>仅影响 127.0.0.1 / ::1</span>
        </div>
        <p className="muted form-indent">这些开关只影响本机浏览器与回调的访问方式，修改后立即生效。</p>
        <label className="check-field">
          <input
            type="checkbox"
            aria-label="本机免密登录"
            disabled={!query.data || saveSetting.isPending}
            checked={Boolean(security.local_bypass ?? true)}
            onChange={event =>
              saveSetting.mutate({
                key: 'auth.local_bypass',
                value: event.target.checked,
                revision: Number(security.local_bypass_revision || 0),
              })
            }
          />
          本机免密登录（默认开启）
        </label>
        <label className="check-field">
          <input
            type="checkbox"
            aria-label="本机 Hook 信任"
            disabled={!query.data || saveSetting.isPending}
            checked={Boolean(security.local_hook_trust ?? true)}
            onChange={event =>
              saveSetting.mutate({
                key: 'hooks.local_trust',
                value: event.target.checked,
                revision: Number(security.local_hook_trust_revision || 0),
              })
            }
          />
          允许本机无 token Hook
        </label>
      </section></> : null}

      {tab === 'automation' ? <AutomationSettings
        profiles={profiles.data?.items || []}
        schedules={schedules.data?.items || []}
        webhooks={webhooks.data?.items || []}
        createdToken={createdToken}
        onCreateSchedule={value => createSchedule.mutate(value)}
        onToggleSchedule={value => toggleSchedule.mutate(value)}
        onDeleteSchedule={value => deleteSchedule.mutate(value)}
        onCreateWebhook={value => createWebhook.mutate(value)}
        onToggleWebhook={value => toggleWebhook.mutate(value)}
        onDeleteWebhook={value => deleteWebhook.mutate(value)}
      /> : null}

      {tab === 'advanced' ? <><div className="tab-list sub-tabs"><button className={`tab-button${panel === 'rules' ? ' active' : ''}`} onClick={() => setParams({ tab: 'advanced', panel: 'rules' })}>规则与别名</button><button className={`tab-button${panel === 'raw' ? ' active' : ''}`} onClick={() => setParams({ tab: 'advanced', panel: 'raw' })}>原始设置</button></div><section className="surface form-surface">
        <div className="surface-title">
          <h2>数据库备份</h2>
          <span>不含媒体文件</span>
        </div>
        <p>备份识别事实、任务、计划与操作历史。</p>
        <button className="secondary" onClick={() => backup.mutate()}>
          <Save size={16} />
          立即创建备份
        </button>
        <DataTable items={backups.data?.items || []} columns={['id', 'kind', 'size', 'sha256', 'created_at']} />
      </section>

      {panel === 'rules' ? <RulesPanel active /> : null}
      {panel === 'raw' ? <section className="surface form-surface">
        <div className="surface-title">
          <h2>高级</h2>
          <button type="button" className="text-button" onClick={() => setShowAdvanced(value => !value)}>
            {showAdvanced ? '收起' : '展开原始设置表'}
          </button>
        </div>
        {showAdvanced ? (
          <>
            <p className="muted form-indent">普通用户无需使用。此处只读展示当前键值，修改请用上方专用表单。</p>
            <DataTable items={query.data?.items || []} columns={['key', 'value', 'revision', 'updated_at']} />
            <DataTable items={query.data?.secrets || []} columns={['key', 'provider', 'updated_at']} />
          </>
        ) : (
          <p className="muted form-indent">已隐藏原始 JSON/密钥表，避免误操作。</p>
        )}
      </section> : null}</> : null}
    </Page>
  )
}

function shortTimestamp(value: unknown) {
  const text = String(value ?? '')
  if (!text) return '—'
  const time = new Date(text).getTime()
  if (!Number.isFinite(time)) return text
  const date = new Date(time)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function displayValue(column: string, value: unknown) {
  if (column === 'kind') return KIND_LABELS[String(value)] || String(value ?? '—')
  if (column === 'job_type') return JOB_TYPE_LABELS[String(value)] || String(value ?? '—')
  if (column === 'enabled') return value === 0 || value === false ? '否' : '是'
  if (column === 'created_at' || column === 'finished_at' || column === 'updated_at') return shortTimestamp(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '—')
}

function DataTable({ items, columns, action, onRow, selectedId }: { items: Item[]; columns: string[]; action?: (item: Item) => React.ReactNode; onRow?: (item: Item) => void; selectedId?: number }) {
  if (!items.length) return <Empty />
  return <div className="table-wrap"><table><thead><tr>{columns.map(column => <th key={column}>{labels[column] || column}</th>)}{action ? <th>操作</th> : null}</tr></thead><tbody>{items.map((item, index) => <tr key={String(item.id ?? item.key ?? index)} className={selectedId === item.id ? 'selected' : ''} onClick={() => onRow?.(item)}>{columns.map(column => <td key={column}>{column === 'status' || column === 'health_status' ? <Status value={String(item[column] || 'unknown')} /> : displayValue(column, item[column])}</td>)}{action ? <td onClick={event => event.stopPropagation()}>{action(item)}</td> : null}</tr>)}</tbody></table></div>
}

const labels: Record<string, string> = { id: 'ID', key: '键', value: '值', size: '大小', sha256: 'SHA-256', enabled: '启用', kind: '类型', path: '路径', health_status: '健康', job_type: '任务', current_stage: '阶段', progress_current: '进度', status: '状态', created_at: '创建时间', finished_at: '完成时间', review_type: '问题类型', canonical_title: '番剧', revision: '修订', updated_at: '更新时间', name: '名称', active_revision_id: '活动版本', source_file: '文件', alias_key: '别名键', source: '来源', confidence: '置信度' }
