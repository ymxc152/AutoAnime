import { FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Plus, RefreshCw, RotateCcw, ScanSearch, Save, ShieldCheck } from 'lucide-react'
import { api } from '../api/client'
import { parseEventStream } from '../api/events'
import { Empty, Page, Status } from '../components/Page'
import { DashboardData, DashboardView } from '../features/dashboard/DashboardView'
import { PlanDetail, PlanWorkspace } from '../features/plans/PlanWorkspace'

type Item = Record<string, any>
type ListResponse = { items: Item[] }
const useList = (key: string, path: string, interval = 5000) => useQuery({ queryKey: [key], queryFn: () => api.get<ListResponse>(path), refetchInterval: interval })

export function DashboardPage() {
  const query = useQuery({ queryKey: ['dashboard'], queryFn: () => api.get<DashboardData>('/dashboard'), refetchInterval: 5000 })
  return <Page title="概览" description="扫描、审核与文件操作的实时运行状态">{query.data ? <DashboardView data={query.data} /> : <Empty>{query.error ? '无法读取系统状态' : '正在载入…'}</Empty>}</Page>
}

const defaultProfile = { name: '', source_root_id: '', library_root_id: '', mode: 'link', execution_policy: 'review_all', min_confidence: 86, stability_seconds: 30, watch_enabled: false, enabled: true }

export function ProfileForm({ initial, roots, editing = false, onSave, onCancel }: { initial: Item; roots: Item[]; editing?: boolean; onSave: (value: Item) => void; onCancel?: () => void }) {
  const [value, setValue] = useState<Item>({ ...initial, watch_enabled: Boolean(initial.watch_enabled), enabled: Boolean(initial.enabled) })
  const sourceRoots = roots.filter(item => item.kind === 'source' && item.enabled !== 0)
  const libraryRoots = roots.filter(item => item.kind === 'library' && item.enabled !== 0)
  const change = (key: string, next: unknown) => setValue(current => ({ ...current, [key]: next }))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSave({ ...value, source_root_id: Number(value.source_root_id), library_root_id: Number(value.library_root_id), min_confidence: Number(value.min_confidence), stability_seconds: Number(value.stability_seconds) })
  }
  return <form className="profile-form profile-editor" onSubmit={submit}>
    <label>配置名称<input aria-label="配置名称" value={value.name} onChange={event => change('name', event.target.value)} /></label>
    {!editing ? <><label>下载源<select aria-label="下载源" value={value.source_root_id} onChange={event => change('source_root_id', event.target.value)}><option value="">选择下载源</option>{sourceRoots.map(root => <option key={root.id} value={root.id}>{root.path}</option>)}</select></label><label>媒体库<select aria-label="媒体库" value={value.library_root_id} onChange={event => change('library_root_id', event.target.value)}><option value="">选择媒体库</option>{libraryRoots.map(root => <option key={root.id} value={root.id}>{root.path}</option>)}</select></label></> : null}
    <label>文件模式<select aria-label="文件模式" value={value.mode} onChange={event => change('mode', event.target.value)}><option value="link">硬链接（保种推荐）</option><option value="copy">复制</option><option value="move">移动</option></select></label>
    <label>执行策略<select aria-label="执行策略" value={value.execution_policy} onChange={event => change('execution_policy', event.target.value)}><option value="review_all">全部审核（推荐起步）</option><option value="auto_apply_safe">安全项自动执行</option><option value="dry_run">仅预览</option></select></label>
    <label>最低置信度<input aria-label="最低置信度" type="number" min="0" max="100" value={value.min_confidence} onChange={event => change('min_confidence', event.target.value)} /></label>
    <label>稳定等待秒数<input aria-label="稳定等待秒数" type="number" min="0" value={value.stability_seconds} onChange={event => change('stability_seconds', event.target.value)} /></label>
    <label className="check-field"><input type="checkbox" checked={Boolean(value.watch_enabled)} onChange={event => change('watch_enabled', event.target.checked)} />启用目录监听</label>
    <label className="check-field"><input type="checkbox" checked={Boolean(value.enabled)} onChange={event => change('enabled', event.target.checked)} />启用此配置</label>
    <div className="form-actions"><button className="primary" disabled={!value.name || (!editing && (!value.source_root_id || !value.library_root_id))}><Save size={16} />{editing ? '保存配置' : '创建扫描配置'}</button>{onCancel ? <button type="button" className="secondary" onClick={onCancel}>取消</button> : null}</div>
  </form>
}

export function ProfilesPage() {
  const roots = useList('roots', '/roots'); const profiles = useList('profiles', '/profiles'); const client = useQueryClient()
  const [kind, setKind] = useState('source'); const [path, setPath] = useState(''); const [editing, setEditing] = useState<Item | null>(null)
  const [pathError, setPathError] = useState('')
  const addRoot = useMutation({ mutationFn: () => api.post('/roots', { kind, path }), onSuccess: async () => { setPath(''); setPathError(''); await client.invalidateQueries({ queryKey: ['roots'] }) }, onError: reason => setPathError(reason instanceof Error ? reason.message : '添加失败') })
  const pickFolder = useMutation({
    mutationFn: () => api.post<{ path: string | null; cancelled: boolean }>('/system/pick-folder', {
      title: kind === 'library' ? '选择媒体库文件夹' : kind === 'operations' ? '选择操作日志文件夹' : '选择下载源文件夹',
      initial_directory: path || undefined,
    }),
    onSuccess: result => {
      setPathError('')
      if (!result.cancelled && result.path) setPath(result.path)
    },
    onError: reason => {
      const msg = reason instanceof Error ? reason.message : ''
      if (/only available on the local machine/i.test(msg)) {
        setPathError('文件夹选择仅本机可用（请用 127.0.0.1 打开控制台，或直接粘贴路径）')
      } else {
        setPathError(msg || '无法打开系统文件夹选择器')
      }
    },
  })
  const validateRoot = useMutation({ mutationFn: (id: number) => api.post(`/roots/${id}/validate`), onSuccess: () => client.invalidateQueries({ queryKey: ['roots'] }) })
  const toggleRoot = useMutation({ mutationFn: (root: Item) => api.patch(`/roots/${root.id}`, { patch: { enabled: !Boolean(root.enabled) } }), onSuccess: () => client.invalidateQueries({ queryKey: ['roots'] }) })
  const addProfile = useMutation({ mutationFn: (profile: Item) => api.post('/profiles', profile), onSuccess: () => client.invalidateQueries({ queryKey: ['profiles'] }) })
  const updateProfile = useMutation({ mutationFn: ({ profile, patch }: { profile: Item; patch: Item }) => api.patch(`/profiles/${profile.id}`, { revision: profile.revision, patch }), onSuccess: async () => { setEditing(null); await client.invalidateQueries({ queryKey: ['profiles'] }) } })
  const scan = useMutation({ mutationFn: (id: number) => api.post('/jobs/scans', { profile_id: id, paths: [] }, { 'Idempotency-Key': `manual-${id}-${Date.now()}` }) })
  return <Page title="扫描配置" description="管理多下载根、媒体库根和逐目录扫描策略。日常使用：选目录 → 建配置 → 手动扫描 → 审核/批准。" actions={<button className="secondary" onClick={() => roots.refetch()}><RefreshCw size={16} />刷新状态</button>}>
    <div className="split"><section className="surface"><div className="surface-title"><h2>存储根目录</h2><span>本机可点“浏览”弹出 Windows 选文件夹；也可手输路径</span></div><form className="inline-form" onSubmit={event => { event.preventDefault(); addRoot.mutate() }}><select aria-label="目录类型" value={kind} onChange={event => setKind(event.target.value)}><option value="source">下载源</option><option value="library">媒体库</option><option value="operations">操作日志</option></select><input aria-label="目录路径" value={path} onChange={event => setPath(event.target.value)} placeholder="F:\动漫下载" /><button type="button" className="secondary" onClick={() => pickFolder.mutate()} disabled={pickFolder.isPending}>{pickFolder.isPending ? '选择中…' : '浏览…'}</button><button className="primary" disabled={!path}><Plus size={16} />添加</button></form>{pathError ? <div className="form-error form-indent">{pathError}</div> : null}<p className="muted form-indent">局域网浏览器无法弹出服务器本机对话框，请直接粘贴路径。下载源与媒体库不能是同一路径或互相嵌套。</p><DataTable items={roots.data?.items || []} columns={['kind', 'path', 'health_status', 'enabled']} action={root => <div className="row-actions"><button className="text-button" onClick={() => validateRoot.mutate(root.id)}>验证</button><button className="text-button" onClick={() => toggleRoot.mutate(root)}>{root.enabled ? '停用' : '启用'}</button></div>} /></section>
      <section className="surface"><div className="surface-title"><h2>扫描策略</h2><span>建议先用“全部审核”，确认无误后再改自动</span></div>{editing ? <ProfileForm key={editing.id} editing initial={editing} roots={roots.data?.items || []} onCancel={() => setEditing(null)} onSave={patch => updateProfile.mutate({ profile: editing, patch: { name: patch.name, mode: patch.mode, execution_policy: patch.execution_policy, min_confidence: patch.min_confidence, stability_seconds: patch.stability_seconds, watch_enabled: patch.watch_enabled, enabled: patch.enabled } })} /> : <ProfileForm initial={defaultProfile} roots={roots.data?.items || []} onSave={profile => addProfile.mutate(profile)} />}{profiles.data?.items.length ? profiles.data.items.map(profile => <div className="profile-row" key={profile.id}><div><strong>{profile.name}</strong><span>{profile.mode} · {profile.execution_policy} · 阈值 {profile.min_confidence}% · rev {profile.revision}</span></div><div className="row-actions"><button className="text-button" onClick={() => setEditing(profile)}>编辑</button><button className="secondary" onClick={() => scan.mutate(profile.id)} disabled={!profile.enabled}><ScanSearch size={16} />手动扫描</button></div></div>) : <Empty>添加源目录和媒体库后创建配置</Empty>}</section></div>
  </Page>
}

export function JobsPage() {
  const query = useList('jobs', '/jobs'); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null)
  const events = useQuery({ queryKey: ['job-events', selected?.id], queryFn: async () => parseEventStream(await api.text(`/jobs/${selected?.id}/events`)), enabled: Boolean(selected) })
  const cancel = useMutation({ mutationFn: (id: number) => api.post(`/jobs/${id}/cancel`), onSuccess: () => client.invalidateQueries({ queryKey: ['jobs'] }) })
  return <Page title="任务中心" description="持久任务、Worker 租约、执行进度与事件记录"><div className="plan-layout"><section className="surface"><DataTable items={query.data?.items || []} columns={['id', 'job_type', 'current_stage', 'progress_current', 'status', 'created_at']} onRow={setSelected} selectedId={selected?.id} action={item => ['queued', 'leased', 'running'].includes(item.status) ? <button className="text-button" onClick={() => cancel.mutate(item.id)}>安全取消</button> : null} /></section><aside className="inspector">{selected ? <><h2>任务 #{selected.id}</h2><Status value={selected.status} /><dl><dt>类型</dt><dd>{selected.job_type}</dd><dt>阶段</dt><dd>{selected.current_stage || '—'}</dd><dt>错误</dt><dd>{selected.error_summary || '—'}</dd></dl><h3>事件记录</h3><div className="event-list">{events.data?.length ? events.data.map(event => <div key={event.sequence}><strong>#{event.sequence} {event.type}</strong><span>{event.message || JSON.stringify(event.payload)}</span></div>) : <Empty>暂无事件</Empty>}</div></> : <Empty>选择任务查看事件</Empty>}</aside></div></Page>
}

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
    <h3>识别证据 JSON</h3>
    <pre className="evidence">{JSON.stringify(evidence, null, 2)}</pre>
    <label>标题<input aria-label="标题" value={title} onChange={event => setTitle(event.target.value)} /></label>
    <label>媒体类型<select aria-label="媒体类型" value={mediaType} onChange={event => changeMediaType(event.target.value as MediaType)}><option value="episode">单集</option><option value="movie">电影</option><option value="special">特别篇 / SP</option></select></label>
    {mediaType !== 'movie' ? <><label>季度<input aria-label="季度" type="number" min="0" step="1" value={season} onChange={event => setSeason(event.target.value)} /></label><label>集号<input aria-label="集号" value={episode} onChange={event => setEpisode(event.target.value)} placeholder={mediaType === 'special' ? '例如 SP01、0、12.5' : '例如 12、12.5、12A'} /></label></> : null}
    <label>版本 / 发布标签<input aria-label="版本 / 发布标签" value={releaseTag} onChange={event => setReleaseTag(event.target.value)} placeholder="例如 WEB-DL、BDRip" /></label>
    <label className="check-field"><input type="checkbox" checked={manualLock} onChange={event => setManualLock(event.target.checked)} />人工锁</label>
    <div className="change-preview"><strong>目标路径预览</strong><span>{preview}</span><small>实际目标会按媒体库根目录、文件扩展名和同集版本冲突规则生成。</small></div>
    <button className="primary full" disabled={!complete || submitting}><Save size={16} />保存并生成新计划</button>
  </form>
}

export function ReviewsPage() {
  const query = useList('reviews', '/reviews'); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null)
  const resolve = useMutation({ mutationFn: (resolution: Item) => api.post(`/reviews/${selected?.id}/resolve`, { resolution }), onSuccess: async () => { setSelected(null); await client.invalidateQueries({ queryKey: ['reviews'] }); await client.invalidateQueries({ queryKey: ['plans'] }) } })
  return <Page title="审核队列" description="检查低置信度、季集缺失、证据冲突和路径冲突"><div className="plan-layout"><section className="surface"><DataTable items={query.data?.items || []} columns={['id', 'review_type', 'status']} onRow={setSelected} selectedId={selected?.id} /></section><aside className="inspector">{selected ? <><h2>审核 #{selected.id}</h2><ReviewResolutionForm key={selected.id} review={selected} onSubmit={resolution => resolve.mutate(resolution)} submitting={resolve.isPending} />{resolve.error ? <p className="error-copy">{resolve.error.message}</p> : null}</> : <Empty>选择一条待审核记录</Empty>}</aside></div></Page>
}

export function PlansPage() {
  const list = useList('plans', '/plans'); const [id, setId] = useState<number | null>(null); const [manualSelection, setManualSelection] = useState(false); const detail = useQuery({ queryKey: ['plan', id], queryFn: () => api.get<PlanDetail>(`/plans/${id}`), enabled: id !== null }); const client = useQueryClient()
  const approve = useMutation({ mutationFn: () => api.post(`/plans/${id}/approve`), onSuccess: async () => { await client.invalidateQueries({ queryKey: ['plans'] }); await detail.refetch() } })
  useEffect(() => { const newest = list.data?.items[0]?.id; if (!manualSelection && newest && id !== Number(newest)) setId(Number(newest)) }, [id, list.data?.items, manualSelection])
  return <Page title="整理计划" description="逐文件核对目标路径；批准后计划不可修改"><div className="plan-tabs">{list.data?.items.map(plan => <button key={plan.id} className={id === plan.id ? 'active' : ''} onClick={() => { setManualSelection(true); setId(plan.id) }}>#{plan.id} <Status value={plan.status} /></button>)}</div>{detail.data ? <PlanWorkspace plan={detail.data} onApprove={() => approve.mutate()} /> : <Empty>暂无整理计划</Empty>}</Page>
}

export function LibraryPage() {
  const query = useList('shows', '/library/shows'); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null); const [title, setTitle] = useState(''); const [reason, setReason] = useState(''); const [locked, setLocked] = useState(true); const [preview, setPreview] = useState<Item | null>(null)
  const detail = useQuery({ queryKey: ['show', selected?.id], queryFn: () => api.get<Item>(`/library/shows/${selected?.id}`), enabled: Boolean(selected) })
  const previewChange = useMutation({ mutationFn: () => api.post<Item>('/library/changes/preview', { show_id: selected?.id, base_revision: selected?.revision, patch: { canonical_title: title, title_locked: locked }, reason }), onSuccess: setPreview })
  const approve = useMutation({ mutationFn: () => api.post<Item>(`/library/changes/${preview?.id}/approve`), onSuccess: async updated => { setPreview(null); setSelected(updated); setTitle(''); setReason(''); await client.invalidateQueries({ queryKey: ['shows'] }); await client.invalidateQueries({ queryKey: ['show', updated.id] }) } })
  const metadata = detail.data?.metadata?.[0]
  return <Page title="资料库" description="查看番剧、季度、文件位置、识别证据和附加元数据"><div className="plan-layout"><section className="surface"><DataTable items={query.data?.items || []} columns={['canonical_title', 'status', 'revision', 'updated_at']} onRow={item => { setSelected(item); setTitle(item.canonical_title); setPreview(null) }} selectedId={selected?.id} /></section><aside className="inspector">{selected ? <>{metadata?.poster_url ? <img className="poster" src={metadata.poster_url} alt={`${selected.canonical_title} 海报`} /> : null}<h2>{selected.canonical_title}</h2><p className="muted-copy">{metadata?.synopsis || '暂无简介；元数据不可用不会影响文件整理。'}</p><dl><dt>放送状态</dt><dd>{metadata?.broadcast_status || '未知'}</dd><dt>季度数</dt><dd>{detail.data?.seasons?.length ?? 0}</dd><dt>修订</dt><dd>{selected.revision}</dd></dl><label>新规范标题<input aria-label="新规范标题" value={title} onChange={event => setTitle(event.target.value)} /></label><label>修改原因<input aria-label="修改原因" value={reason} onChange={event => setReason(event.target.value)} /></label><label className="check-field"><input type="checkbox" checked={locked} onChange={event => setLocked(event.target.checked)} />锁定人工标题</label>{preview ? <div className="change-preview"><strong>修改预览</strong><span>{String(preview.old_values?.canonical_title)} → {String(preview.new_values?.canonical_title)}</span><button className="primary full" onClick={() => approve.mutate()}><ShieldCheck size={16} />批准修改</button></div> : <button className="secondary full" disabled={!title || !reason || title === selected.canonical_title} onClick={() => previewChange.mutate()}><Check size={16} />预览修改</button>}</> : <Empty>选择番剧查看详情和纠正</Empty>}</aside></div></Page>
}

export function RulesPage() {
  const query = useList('rules', '/rules', 0); const client = useQueryClient(); const [name, setName] = useState(''); const [selectedId, setSelectedId] = useState<number | null>(null); const [document, setDocument] = useState('{\n  "aliases": {}\n}'); const [error, setError] = useState('')
  const selected = query.data?.items.find(item => item.id === selectedId) || query.data?.items[0]
  useEffect(() => { if (selectedId === null && query.data?.items[0]?.id) setSelectedId(Number(query.data.items[0].id)) }, [query.data?.items, selectedId])
  const refresh = () => client.invalidateQueries({ queryKey: ['rules'] })
  const createSet = useMutation({ mutationFn: () => api.post<Item>('/rules', { name }), onSuccess: async item => { setName(''); setSelectedId(item.id); await refresh() } })
  const createRevision = useMutation({ mutationFn: async () => { setError(''); let parsed: Item; try { parsed = JSON.parse(document) } catch { throw new Error('规则 JSON 格式无效') } return api.post('/rules/revisions', { rule_set_id: selected?.id, document: parsed }) }, onSuccess: refresh, onError: reason => setError(reason instanceof Error ? reason.message : '保存失败') })
  const validate = useMutation({ mutationFn: (id: number) => api.post(`/rules/revisions/${id}/validate`), onSuccess: refresh })
  const activate = useMutation({ mutationFn: (id: number) => api.post(`/rules/revisions/${id}/activate`), onSuccess: refresh })
  const rollback = useMutation({ mutationFn: (id: number) => api.post(`/rules/${selected?.id}/revisions/${id}/rollback`), onSuccess: refresh })
  const latest = selected?.revisions?.[0]
  return <Page title="规则与别名" description="JSON 规则以不可变修订保存，必须先校验再激活"><div className="plan-layout"><section className="surface"><form className="inline-form" onSubmit={event => { event.preventDefault(); createSet.mutate() }}><input aria-label="规则集名称" value={name} onChange={event => setName(event.target.value)} placeholder="例如：默认别名" /><button className="primary" disabled={!name}><Plus size={16} />新建规则集</button></form><DataTable items={query.data?.items || []} columns={['id', 'name', 'active_revision_id', 'updated_at']} onRow={item => setSelectedId(item.id)} selectedId={selected?.id} /></section><aside className="inspector">{selected ? <><h2>{selected.name}</h2><label>规则 JSON<textarea aria-label="规则 JSON" value={document} onChange={event => setDocument(event.target.value)} /></label>{error ? <div className="form-error">{error}</div> : null}<button className="secondary full" onClick={() => createRevision.mutate()}><Save size={16} />保存草稿</button><div className="revision-list">{selected.revisions?.map((revision: Item) => <div key={revision.id}><strong>rev {revision.revision}</strong><Status value={revision.status} /><div className="row-actions">{revision.status === 'draft' ? <button className="text-button" onClick={() => validate.mutate(revision.id)}>校验</button> : null}{revision.status === 'validated' ? <button className="text-button" onClick={() => activate.mutate(revision.id)}>激活</button> : null}{revision.content_hash && revision.status !== 'active' ? <button className="text-button" onClick={() => rollback.mutate(revision.id)}>回退到此版</button> : null}</div></div>)}</div>{latest ? <pre className="evidence">{JSON.stringify(latest.document, null, 2)}</pre> : <Empty>尚无修订</Empty>}</> : <Empty>先创建规则集</Empty>}</aside></div></Page>
}

export function OperationsPage() {
  const query = useList('operations', '/operations'); const client = useQueryClient(); const [selected, setSelected] = useState<Item | null>(null); const detail = useQuery({ queryKey: ['operation', selected?.id], queryFn: () => api.get<Item>(`/operations/${selected?.id}`), enabled: Boolean(selected) })
  const rollback = useMutation({ mutationFn: (id: number) => api.post(`/operations/${id}/rollback`), onSuccess: async () => { await client.invalidateQueries({ queryKey: ['operations'] }); await detail.refetch() } })
  return <Page title="操作历史" description="执行、自动补偿、手动回滚和逐文件摘要"><div className="plan-layout"><section className="surface"><DataTable items={query.data?.items || []} columns={['id', 'kind', 'status', 'created_at', 'finished_at']} onRow={setSelected} selectedId={selected?.id} action={item => item.kind === 'execute' && item.status === 'completed' ? <button className="text-button danger" onClick={() => rollback.mutate(item.id)}><RotateCcw size={14} />回滚</button> : null} /></section><aside className="inspector">{detail.data ? <><h2>批次 #{detail.data.id}</h2><Status value={detail.data.status} /><pre className="evidence">{JSON.stringify(detail.data.summary, null, 2)}</pre><div className="event-list">{detail.data.items?.map((item: Item) => <div key={item.id}><strong>{item.action} · {item.status}</strong><span>{item.source_path} → {item.destination_path}</span></div>)}</div></> : <Empty>选择批次查看文件摘要</Empty>}</aside></div></Page>
}

export function AutomationSettings({ profiles, schedules, webhooks, createdToken, onCreateSchedule, onToggleSchedule, onCreateWebhook, onToggleWebhook }: {
  profiles: Item[]
  schedules: Item[]
  webhooks: Item[]
  createdToken: string
  onCreateSchedule: (value: Item) => void
  onToggleSchedule: (value: Item) => void
  onCreateWebhook: (value: Item) => void
  onToggleWebhook: (value: Item) => void
}) {
  const [scheduleProfile, setScheduleProfile] = useState(String(profiles[0]?.id || ''))
  const [intervalMinutes, setIntervalMinutes] = useState(15)
  const [webhookProfile, setWebhookProfile] = useState(String(profiles[0]?.id || ''))
  const [webhookName, setWebhookName] = useState('qBittorrent')
  useEffect(() => {
    if (!scheduleProfile && profiles[0]) setScheduleProfile(String(profiles[0].id))
    if (!webhookProfile && profiles[0]) setWebhookProfile(String(profiles[0].id))
  }, [profiles, scheduleProfile, webhookProfile])
  const profileName = (id: number) => profiles.find(profile => Number(profile.id) === Number(id))?.name || `#${id}`
  const presets = [5, 15, 30, 60, 120]
  return <section className="surface form-surface automation-settings">
    <div className="surface-title"><h2>自动扫描</h2><span>定时计划与下载器回调（密钥/路径仅本机）</span></div>
    <p className="muted form-indent">创建计划后由 Worker 按间隔扫描绑定配置；Webhook 给 qBittorrent 等下载完成回调用。</p>
    <div className="automation-grid">
      <div className="automation-card">
        <div className="automation-card-head"><h3>定时计划</h3><span>按固定间隔自动扫描</span></div>
        <div className="automation-fields">
          <label>扫描配置<select aria-label="计划扫描配置" value={scheduleProfile} onChange={event => setScheduleProfile(event.target.value)}>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
          <label>扫描间隔
            <div className="chip-row" role="group" aria-label="间隔预设">{presets.map(minutes => <button type="button" key={minutes} className={intervalMinutes === minutes ? 'chip active' : 'chip'} onClick={() => setIntervalMinutes(minutes)}>{minutes < 60 ? `${minutes} 分钟` : `${minutes / 60} 小时`}</button>)}</div>
            <input aria-label="间隔分钟" type="number" min={1} value={intervalMinutes} onChange={event => setIntervalMinutes(Number(event.target.value))} />
          </label>
          <div className="form-actions"><button className="secondary" disabled={!scheduleProfile || intervalMinutes < 1} onClick={() => onCreateSchedule({ profile_id: Number(scheduleProfile), kind: 'interval', schedule: { interval_minutes: intervalMinutes }, timezone: 'UTC' })}><Plus size={16} />创建计划</button></div>
        </div>
        <div className="automation-list">{schedules.length ? schedules.map(schedule => <div className="automation-item" key={schedule.id}><div className="automation-item-main"><strong>{schedule.kind === 'interval' ? `每 ${schedule.schedule.interval_minutes} 分钟` : `每天 ${schedule.schedule.time}`}</strong><span>{profileName(schedule.profile_id)} · 下次 {schedule.next_run_at || '已停用'}</span></div><button className="text-button" aria-label={schedule.enabled ? '停用计划' : '启用计划'} onClick={() => onToggleSchedule(schedule)}>{schedule.enabled ? '停用' : '启用'}</button></div>) : <Empty>还没有定时计划</Empty>}</div>
      </div>
      <div className="automation-card">
        <div className="automation-card-head"><h3>下载器 Webhook</h3><span>下载完成后回调触发扫描</span></div>
        <div className="automation-fields">
          <label>名称<input aria-label="Webhook 名称" value={webhookName} onChange={event => setWebhookName(event.target.value)} /></label>
          <label>绑定配置<select aria-label="Webhook 扫描配置" value={webhookProfile} onChange={event => setWebhookProfile(event.target.value)}>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
          <div className="form-actions"><button className="secondary" disabled={!webhookName || !webhookProfile} onClick={() => onCreateWebhook({ name: webhookName, downloader: 'qbittorrent', profile_id: Number(webhookProfile) })}><Plus size={16} />创建 Webhook</button></div>
        </div>
        {createdToken ? <div className="change-preview"><strong>Token 仅显示一次，请立即保存</strong><code>{createdToken}</code></div> : null}
        <div className="automation-list">{webhooks.length ? webhooks.map(webhook => <div className="automation-item" key={webhook.id}><div className="automation-item-main"><strong>{webhook.name}</strong><span>{profileName(webhook.profile_id)} · 最后调用 {webhook.last_called_at || '尚未调用'}</span></div><button className="text-button" aria-label={webhook.enabled ? '停用 Webhook' : '启用 Webhook'} onClick={() => onToggleWebhook(webhook)}>{webhook.enabled ? '停用' : '启用'}</button></div>) : <Empty>还没有 Webhook</Empty>}</div>
      </div>
    </div>
  </section>
}

export function SettingsPage() {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<{ items: Item[]; secrets: Item[]; security?: Item; openai?: Item }>('/settings'),
  })
  const backups = useList('backups', '/backups', 0)
  const schedules = useList('schedules', '/schedules', 0)
  const webhooks = useList('webhook-sources', '/webhook-sources', 0)
  const profiles = useList('automation-profiles', '/profiles', 0)
  const client = useQueryClient()
  const [openaiKey, setOpenaiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [timeout, setTimeoutSeconds] = useState('30')
  const [aiError, setAiError] = useState('')
  const [aiMessage, setAiMessage] = useState('')
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
      await client.invalidateQueries({ queryKey: ['settings'] })
      await client.invalidateQueries({ queryKey: ['bootstrap-status'] })
    },
  })
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
  const backup = useMutation({
    mutationFn: () => api.post('/backups'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['backups'] }),
  })
  const createSchedule = useMutation({
    mutationFn: (value: Item) => api.post('/schedules', value),
    onSuccess: () => client.invalidateQueries({ queryKey: ['schedules'] }),
  })
  const toggleSchedule = useMutation({
    mutationFn: (value: Item) =>
      api.patch(`/schedules/${value.id}`, {
        revision: value.revision,
        patch: { enabled: !Boolean(value.enabled) },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['schedules'] }),
  })
  const createWebhook = useMutation({
    mutationFn: (value: Item) => api.post<Item>('/webhook-sources', value),
    onSuccess: async value => {
      setCreatedToken(String(value.token || ''))
      await client.invalidateQueries({ queryKey: ['webhook-sources'] })
    },
  })
  const toggleWebhook = useMutation({
    mutationFn: (value: Item) =>
      api.patch(`/webhook-sources/${value.id}`, {
        revision: value.revision,
        patch: { enabled: !Boolean(value.enabled) },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['webhook-sources'] }),
  })

  return (
    <Page title="系统设置" description="用表单修改常用项；开关立即生效，AI 参数点保存。密钥只存本机，不会回显或提交。">
      <section className="surface form-surface">
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
        <label className="check-field">
          <input
            type="checkbox"
            aria-label="启用 AI 识别"
            checked={Boolean(openai.enabled)}
            onChange={event =>
              saveSetting.mutate({
                key: 'openai.enabled',
                value: event.target.checked,
                revision: Number(openai.enabled_revision || 0),
              })
            }
          />
          启用 OpenAI 兼容识别（仅补充本地未识别项，结果仍经本地安全策略校验）
        </label>
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
      </section>

      <AutomationSettings
        profiles={profiles.data?.items || []}
        schedules={schedules.data?.items || []}
        webhooks={webhooks.data?.items || []}
        createdToken={createdToken}
        onCreateSchedule={value => createSchedule.mutate(value)}
        onToggleSchedule={value => toggleSchedule.mutate(value)}
        onCreateWebhook={value => createWebhook.mutate(value)}
        onToggleWebhook={value => toggleWebhook.mutate(value)}
      />

      <section className="surface form-surface">
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

      <section className="surface form-surface">
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
      </section>
    </Page>
  )
}

function displayValue(value: unknown) {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '—')
}

function DataTable({ items, columns, action, onRow, selectedId }: { items: Item[]; columns: string[]; action?: (item: Item) => React.ReactNode; onRow?: (item: Item) => void; selectedId?: number }) {
  if (!items.length) return <Empty />
  return <div className="table-wrap"><table><thead><tr>{columns.map(column => <th key={column}>{labels[column] || column}</th>)}{action ? <th>操作</th> : null}</tr></thead><tbody>{items.map((item, index) => <tr key={String(item.id ?? item.key ?? index)} className={selectedId === item.id ? 'selected' : ''} onClick={() => onRow?.(item)}>{columns.map(column => <td key={column}>{column === 'status' || column === 'health_status' ? <Status value={String(item[column] || 'unknown')} /> : displayValue(item[column])}</td>)}{action ? <td onClick={event => event.stopPropagation()}>{action(item)}</td> : null}</tr>)}</tbody></table></div>
}

const labels: Record<string, string> = { id: 'ID', key: '键', value: '值', size: '大小', sha256: 'SHA-256', enabled: '启用', kind: '类型', path: '路径', health_status: '健康', job_type: '任务', current_stage: '阶段', progress_current: '进度', status: '状态', created_at: '创建时间', finished_at: '完成时间', review_type: '问题类型', canonical_title: '番剧', revision: '修订', updated_at: '更新时间', name: '名称', active_revision_id: '活动版本' }
