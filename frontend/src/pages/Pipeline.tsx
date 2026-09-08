/*
 * Pipeline 页 —— @xyflow/react 三级识别管线节点图(L1→L2→L3→arbiter→organize)。
 * 每节点:实时命中/通过徽标(基线来自 GET /api/metrics.levels + SSE 事件累加);
 * SSE 驱动文件流:事件到达后沿路径逐段点亮边(xyflow animated edge),
 * 侧栏展示最近事件流。流量模型见 pipelineFlow.ts(纯 reducer)。
 */
import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { FlaskConical, FolderDown, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { useEventStream } from '../hooks/eventStreamContext'
import { strings, t } from '../strings'
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  PageTitle,
  Select,
  StatusDot,
  Switch,
} from '../components'
import {
  activeEdgesOf,
  flowReducer,
  initialFlowState,
  passingNodes,
  pipelineBaseline,
  NODE_META,
  EDGE_DEFS,
  type PipelineNodeData,
  type PipelineNodeId,
} from './pipelineFlow'
import type {
  ParsePreviewResponse,
  PipelineTask,
  SchedulerRunResponse,
  SchedulerScope,
  SseEvent,
} from '../api/types'

function PipelineNodeView({ data }: NodeProps<Node<PipelineNodeData>>) {
  const rate = data.entered > 0 ? Math.round((data.passed / data.entered) * 100) : null
  return (
    <div
      data-testid={`pipeline-node-${data.title}`}
      className={`w-40 rounded-md border bg-surface px-3 py-2 shadow-soft-md ${
        data.passing > 0 ? 'border-primary' : 'border-line'
      }`}
    >
      <p className="text-sm font-medium text-ink">{data.title}</p>
      <p className="mt-0.5 text-xs text-ink-secondary">{data.desc}</p>
      <div className="mt-1.5 flex items-center gap-1.5">
        <Badge tone={data.passing > 0 ? 'primary' : 'neutral'} mark>
          <span className="data-text" data-testid={`node-count-${data.title}`}>
            {data.passed}
          </span>
        </Badge>
        {rate !== null && (
          <span className="text-xs text-ink-secondary data-text">
            {strings.pipeline.badge.hitRate} {rate}%
          </span>
        )}
      </div>
      {data.passing > 0 && <p className="data-text mt-1 text-xs text-primary">{data.passing} 个文件经过</p>}
    </div>
  )
}

const nodeTypes: NodeTypes = { pipeline: PipelineNodeView }

const categoryTone: Record<
  SseEvent['category'],
  'success' | 'info' | 'warning' | 'danger' | 'neutral'
> = {
  parse: 'info',
  download: 'neutral',
  organize: 'success',
  error: 'danger',
  notify: 'warning',
  system: 'neutral',
}

const STEP_MS = 900

type SegmentKey = keyof typeof strings.pending.segment

/**
 * UXfix:试跑结果结构化小卡片(替代裸 JSON.stringify)。
 * 字段口径对齐后端 ParsePreviewOut(route/result{title,season,episode,segment,
 * fansub,level,confidence,missing_fields,evidence},见 web/routers/pipeline.py)。
 */
function PreviewResult({ preview }: { preview: ParsePreviewResponse }) {
  const result = preview.result
  if (result === null) {
    return <p className="text-xs text-ink-secondary">{strings.uxfix.parsePreviewEmpty}</p>
  }
  const segment = strings.pending.segment[result.segment as SegmentKey] ?? result.segment
  const verdict =
    result.level === 'high'
      ? strings.uxfix.verdictHigh
      : result.level === 'medium'
        ? strings.uxfix.verdictMedium
        : result.level === 'low'
          ? strings.uxfix.verdictLow
          : result.level
  return (
    <div className="rounded-sm bg-surface-2 px-2 py-1.5 text-xs" data-testid="preview-result-card">
      <p className="font-medium text-ink">{strings.uxfix.parsePreviewTitle}</p>
      <dl className="data-text mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
        <dt className="text-ink-secondary">{strings.pending.fieldTitle}</dt>
        <dd className="text-ink">{result.title}</dd>
        <dt className="text-ink-secondary">{strings.pending.fieldSeason}</dt>
        <dd className="text-ink">{result.season ?? '—'}</dd>
        <dt className="text-ink-secondary">{strings.pending.fieldEpisode}</dt>
        <dd className="text-ink">{result.episode ?? '—'}</dd>
        <dt className="text-ink-secondary">{strings.pending.fieldSegment}</dt>
        <dd className="text-ink">{segment}</dd>
        <dt className="text-ink-secondary">{strings.pending.fieldFansub}</dt>
        <dd className="text-ink">{result.fansub ?? '—'}</dd>
        <dt className="text-ink-secondary">{strings.uxfix.previewConfidenceLabel}</dt>
        <dd className="text-ink">
          {result.level} · {Math.round(result.confidence * 100)}%
        </dd>
        <dt className="text-ink-secondary">{strings.uxfix.previewVerdictLabel}</dt>
        <dd className="text-ink">{verdict}</dd>
      </dl>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-ink-secondary">{strings.uxfix.showRawJson}</summary>
        <pre className="data-text mt-1 overflow-x-auto text-xs">{JSON.stringify(preview.result, null, 2)}</pre>
      </details>
    </div>
  )
}


function ManualOperations() {
  const [parseName, setParseName] = useState('')
  // 12-F:parse-preview 恢复 folder/parent 可选上下文输入(后端 ParsePreviewIn.folder/parent)
  const [parseFolder, setParseFolder] = useState('')
  const [parseParent, setParseParent] = useState('')
  const [preview, setPreview] = useState<ParsePreviewResponse | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  // UXfix:空文件名不再静默 no-op,行内提示必填
  const [parseNameError, setParseNameError] = useState<string | null>(null)

  const [directory, setDirectory] = useState('')
  const [dryRun, setDryRun] = useState(true)
  const [task, setTask] = useState<PipelineTask | null>(null)
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  // UXfix:空导入路径不再静默 no-op,行内提示必填
  const [importDirError, setImportDirError] = useState<string | null>(null)

  // 12-F:run-once scope 三选(后端 SchedulerRunIn.scope: all/rss/download),默认 all
  const [scope, setScope] = useState<SchedulerScope>('all')
  const [schedulerResult, setSchedulerResult] = useState<SchedulerRunResponse | null>(null)
  const [schedulerBusy, setSchedulerBusy] = useState(false)
  const [schedulerError, setSchedulerError] = useState<string | null>(null)

  const submitPreview = async (): Promise<void> => {
    if (parseName.trim() === '') {
      setParseNameError(strings.uxfix.parseNameRequired)
      return
    }
    setParseNameError(null)
    setPreviewBusy(true)
    setPreviewError(null)
    try {
      // 12-F:folder/parent 传空不传(可选字段,回退纯文件名解析)
      const folder = parseFolder.trim()
      const parent = parseParent.trim()
      setPreview(
        await api.pipeline.parsePreview({
          name: parseName.trim(),
          ...(folder !== '' ? { folder } : {}),
          ...(parent !== '' ? { parent } : {}),
        }),
      )
    } catch (cause) {
      setPreviewError(cause instanceof Error ? cause.message : strings.common.actionFailed)
    } finally {
      setPreviewBusy(false)
    }
  }

  const waitForTask = async (taskId: string): Promise<void> => {
    for (let round = 0; round < 600; round += 1) {
      const current = await api.pipeline.task(taskId)
      setTask(current)
      if (current.status !== 'running') {
        if (current.status === 'failed') throw new Error(current.error ?? strings.common.actionFailed)
        return
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500))
    }
    throw new Error(strings.common.actionFailed)
  }

  const submitImport = async (): Promise<void> => {
    if (directory.trim() === '') {
      setImportDirError(strings.uxfix.importPathRequired)
      return
    }
    setImportDirError(null)
    setImportBusy(true)
    setImportError(null)
    try {
      const started = await api.pipeline.startImport({
        directory: directory.trim(),
        dry_run: dryRun,
      })
      await waitForTask(started.task_id)
    } catch (cause) {
      setImportError(cause instanceof Error ? cause.message : strings.common.actionFailed)
    } finally {
      setImportBusy(false)
    }
  }

  const runOnce = async (): Promise<void> => {
    setSchedulerBusy(true)
    setSchedulerError(null)
    try {
      // 12-F:按选择传 scope(all/rss/download),默认 all
      setSchedulerResult(await api.scheduler.runOnce({ scope }))
    } catch (cause) {
      setSchedulerError(cause instanceof Error ? cause.message : strings.common.actionFailed)
    } finally {
      setSchedulerBusy(false)
    }
  }

  return (
    <Card title={strings.pipeline.manualSection} className="mb-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void submitPreview()
          }}
        >
          <Field
            label={strings.pipeline.parseName}
            description={strings.pipeline.parseNameHint}
            htmlFor="pipeline-parse-name"
            error={parseNameError}
          >
            <Input
              id="pipeline-parse-name"
              value={parseName}
              onChange={(e) => setParseName(e.target.value)}
              placeholder="Show S01E01 1080p.mkv"
            />
          </Field>
          {/* 12-F:folder = 文件所在目录名,parent = 包含该文件的完整目录路径,均可选 */}
          <Field label={strings.pipeline.parseFolder} description={strings.pipeline.parseFolderHint} htmlFor="pipeline-parse-folder">
            <Input
              id="pipeline-parse-folder"
              value={parseFolder}
              onChange={(e) => setParseFolder(e.target.value)}
              placeholder={strings.pipeline.parseFolderPlaceholder}
            />
          </Field>
          <Field label={strings.pipeline.parseParent} description={strings.pipeline.parseParentHint} htmlFor="pipeline-parse-parent">
            <Input
              id="pipeline-parse-parent"
              value={parseParent}
              onChange={(e) => setParseParent(e.target.value)}
              placeholder={strings.pipeline.parseParentPlaceholder}
              className="data-text"
            />
          </Field>
          <Button type="submit" variant="secondary" loading={previewBusy}>
            <FlaskConical aria-hidden className="h-4 w-4" />
            {strings.pipeline.parsePreview}
          </Button>
          {previewError !== null && <p role="alert" className="text-xs text-danger">{previewError}</p>}
          {preview !== null && <PreviewResult preview={preview} />}
        </form>

        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void submitImport()
          }}
        >
          <Field label={strings.pipeline.importDirectory} htmlFor="pipeline-import-directory" error={importDirError}>
            <Input
              id="pipeline-import-directory"
              value={directory}
              onChange={(e) => setDirectory(e.target.value)}
              className="data-text"
              placeholder="D:\downloads\番剧"
            />
          </Field>
          <div className="flex items-center justify-between gap-2 py-0.5">
            <span className="text-sm leading-none text-ink">{strings.pipeline.dryRun}</span>
            <Switch checked={dryRun} onChange={setDryRun} aria-label={strings.pipeline.dryRun} />
          </div>
          <Button type="submit" variant="primary" loading={importBusy}>
            <FolderDown aria-hidden className="h-4 w-4" />
            {strings.pipeline.startImport}
          </Button>
          {importError !== null && <p role="alert" className="text-xs text-danger">{importError}</p>}
          {task !== null && (
            <p className="text-xs text-ink-secondary data-text">
              {task.status === 'running'
                ? // UXfix:轮询到 processed/total 时显示导入进度
                  task.total !== null
                  ? `${strings.pipeline.running} · ${t(strings.uxfix.importProgress, { done: task.processed, total: task.total })}`
                  : strings.pipeline.running
                : task.status === 'completed'
                  ? strings.pipeline.completed
                  : strings.pipeline.failed}
              {task.summary !== null &&
                ` · ${task.summary.archived}/${task.summary.scanned}`}
              {task.error !== null && ` · ${task.error}`}
            </p>
          )}
        </form>

        <div className="flex flex-col gap-2">
          {/* 12-F:run-once scope 三选,默认全部(全部/仅 RSS 轮询/仅下载对账) */}
          <Field label={strings.pipeline.schedulerScopeLabel} htmlFor="pipeline-scheduler-scope">
            <Select
              id="pipeline-scheduler-scope"
              value={scope}
              onChange={(e) => setScope(e.target.value as SchedulerScope)}
            >
              <option value="all">{strings.pipeline.schedulerScopeAll}</option>
              <option value="rss">{strings.pipeline.schedulerScopeRss}</option>
              <option value="download">{strings.pipeline.schedulerScopeDownload}</option>
            </Select>
          </Field>
          <Button variant="secondary" loading={schedulerBusy} onClick={() => void runOnce()}>
            <RefreshCw aria-hidden className="h-4 w-4" />
            {strings.pipeline.runOnce}
          </Button>
          {schedulerError !== null && <p role="alert" className="text-xs text-danger">{schedulerError}</p>}
          {schedulerResult !== null && (
            // UXfix:run-once 结果轻量摘要(数值统计逐项列出),原始 JSON 折叠收起
            <div className="rounded-sm bg-surface-2 px-2 py-1.5 text-xs" data-testid="run-once-summary">
              <ul className="flex flex-col gap-0.5">
                {Object.entries(schedulerResult.reports).map(([key, report]) => {
                  const stats = Object.entries(report)
                    .filter((entry): entry is [string, number] => typeof entry[1] === 'number')
                    .map(([statKey, value]) => `${statKey} ${value}`)
                  return (
                    <li key={key} className="data-text text-ink">
                      {key}
                      {stats.length > 0 && ` · ${stats.join(' · ')}`}
                    </li>
                  )
                })}
              </ul>
              {schedulerResult.errors.length > 0 && (
                <p className="mt-1 text-danger">
                  {t(strings.uxfix.savedWarnings, { n: schedulerResult.errors.length })}
                </p>
              )}
              <details className="mt-1.5">
                <summary className="cursor-pointer text-ink-secondary">{strings.uxfix.showRawJson}</summary>
                <pre className="data-text mt-1 overflow-x-auto text-xs">{JSON.stringify(schedulerResult, null, 2)}</pre>
              </details>
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

/**
 * 12-F:人工确认命名(POST /api/pipeline/confirm-name)。
 * 用途:对不在待确认队列里的文件名做人工确认,结果写入识别记忆,
 * 下次同类命名直接命中。name 必填;其余字段可选覆写(空 = 回退 L1 草稿)。
 */
function ConfirmNameCard() {
  const [name, setName] = useState('')
  const [title, setTitle] = useState('')
  const [season, setSeason] = useState('')
  const [episode, setEpisode] = useState('')
  const [segment, setSegment] = useState('')
  const [fansub, setFansub] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (): Promise<void> => {
    if (name.trim() === '') {
      setError(strings.ops12f.confirmNameRequired)
      return
    }
    setBusy(true)
    setError(null)
    try {
      // 可选覆写:留空不传(等价后端缺省回退 L1 草稿)
      const seasonNum = Number(season)
      const episodeNum = Number(episode)
      const out = await api.pipeline.confirmName({
        name: name.trim(),
        ...(title.trim() !== '' ? { title: title.trim() } : {}),
        ...(season !== '' && Number.isFinite(seasonNum) ? { season: seasonNum } : {}),
        ...(episode !== '' && Number.isFinite(episodeNum) ? { episode: episodeNum } : {}),
        ...(segment !== '' ? { segment } : {}),
        ...(fansub.trim() !== '' ? { fansub: fansub.trim() } : {}),
      })
      // 反馈 = 学习结果 + 是否已归档(按响应字段组织,不编造)
      const archiveNote =
        out.archive.archived && typeof out.archive.dst === 'string'
          ? t(strings.ops12f.confirmNameArchived, { dst: out.archive.dst })
          : t(strings.ops12f.confirmNameNotArchived, {
              reason:
                typeof out.archive.reason === 'string' ? out.archive.reason : strings.common.unknown,
            })
      toast.success(
        `${t(strings.ops12f.confirmNameDone, { entries: out.entries.length })} · ${archiveNote}`,
      )
      setName('')
      setTitle('')
      setSeason('')
      setEpisode('')
      setSegment('')
      setFansub('')
    } catch (cause) {
      // 422 等校验失败展示后端原因
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title={strings.ops12f.confirmNameTitle} description={strings.ops12f.confirmNameHint} className="mb-4">
      <form
        className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-6 lg:items-start"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field
          label={strings.ops12f.confirmNameField}
          htmlFor="confirm-name-file"
          className="lg:col-span-2"
          error={error}
        >
          <Input
            id="confirm-name-file"
            value={name}
            onChange={(e) => setName(e.target.value)}
            invalid={error !== null}
            placeholder="Show S01E01 1080p.mkv"
            className="data-text"
          />
        </Field>
        <Field label={strings.pending.fieldTitle} htmlFor="confirm-name-title">
          <Input
            id="confirm-name-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={strings.ops12f.confirmNameOptionalHint}
          />
        </Field>
        <Field label={strings.pending.fieldSeason} htmlFor="confirm-name-season">
          <Input
            id="confirm-name-season"
            type="number"
            value={season}
            onChange={(e) => setSeason(e.target.value)}
          />
        </Field>
        <Field label={strings.pending.fieldEpisode} htmlFor="confirm-name-episode">
          <Input
            id="confirm-name-episode"
            type="number"
            value={episode}
            onChange={(e) => setEpisode(e.target.value)}
          />
        </Field>
        <Field label={strings.pending.fieldSegment} htmlFor="confirm-name-segment">
          <Select
            id="confirm-name-segment"
            value={segment}
            onChange={(e) => setSegment(e.target.value)}
          >
            <option value="">{strings.ops12f.confirmNameSegmentEmpty}</option>
            <option value="episode">{strings.pending.segment.episode}</option>
            <option value="season_pack">{strings.pending.segment.season_pack}</option>
            <option value="movie">{strings.pending.segment.movie}</option>
          </Select>
        </Field>
        <Field label={strings.pending.fieldFansub} htmlFor="confirm-name-fansub">
          <Input
            id="confirm-name-fansub"
            value={fansub}
            onChange={(e) => setFansub(e.target.value)}
          />
        </Field>
        <div className="lg:col-span-6">
          <Button type="submit" variant="primary" loading={busy}>
            {strings.ops12f.submitConfirmName}
          </Button>
        </div>
      </form>
    </Card>
  )
}

export function PipelinePage() {
  const fetcher = useCallback(() => api.metrics.get(), [])
  const { data: metrics } = useApi(fetcher)
  const [state, dispatch] = useReducer(flowReducer, initialFlowState)
  const { subscribe, status } = useEventStream()

  // 订阅全局事件流(App 挂载的单条 SSE 连接),事件驱动文件流
  useEffect(
    () =>
      subscribe((event) => {
        dispatch({ type: 'event', event })
      }),
    [subscribe],
  )

  useEffect(() => {
    if (metrics !== null) {
      // 基线由 /api/metrics.by_level 派生(L1/L2/L3 各级解析数)
      dispatch({ type: 'seed', baseline: pipelineBaseline(metrics) })
    }
  }, [metrics])

  useEffect(() => {
    const timer = setInterval(() => {
      dispatch({ type: 'tick' })
    }, STEP_MS)
    return () => clearInterval(timer)
  }, [])

  const nodes = useMemo<Node<PipelineNodeData>[]>(() => {
    const passing = passingNodes(state.tokens)
    return (Object.keys(NODE_META) as PipelineNodeId[]).map((id) => {
      const meta = NODE_META[id]
      return {
        id,
        type: 'pipeline' as const,
        position: { x: meta.x, y: meta.y },
        data: {
          title: meta.title,
          desc: meta.desc,
          passed: state.counters[id] ?? 0,
          entered: state.entered[id] ?? 0,
          passing: passing.has(id) ? 1 : 0,
        },
      }
    })
  }, [state.counters, state.tokens, state.entered])

  const activeEdges = useMemo(() => activeEdgesOf(state.tokens), [state.tokens])

  const edges = useMemo<Edge[]>(
    () =>
      EDGE_DEFS.map((def) => {
        const active = activeEdges.has(def.id)
        return {
          id: def.id,
          source: def.source,
          target: def.target,
          animated: active,
          style: active
            ? { stroke: 'var(--ink-primary)', strokeWidth: 2 }
            : { stroke: 'var(--ink-border)', strokeWidth: 1.5 },
        }
      }),
    [activeEdges],
  )

  return (
    <>
      <PageTitle
        title={strings.pipeline.title}
        description={strings.pipeline.subtitle}
        actions={
          <>
            <StatusDot
              size={7}
              tone={status === 'open' ? 'success' : status === 'closed' ? 'neutral' : 'warning'}
              label={status === 'open' ? strings.pipeline.flow.live : strings.pipeline.flow.offline}
            />
            <Button size="sm" variant="ghost" onClick={() => dispatch({ type: 'clear' })}>
              {strings.pipeline.flow.clear}
            </Button>
          </>
        }
      />

      <ManualOperations />
      {/* 12-F:手动操作区第三张卡 —— 库外文件名的人工确认与学习 */}
      <ConfirmNameCard />

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_280px]">
        <Card flush className="hidden overflow-hidden lg:block">
          <div className="h-[420px]">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              minZoom={0.4}
            >
              <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
            </ReactFlow>
          </div>
        </Card>

        {/* 移动端:ReactFlow 缩放后节点文字不可读,降级为纵向步骤列表 */}
        <Card title={strings.pipeline.flowSteps} className="lg:hidden">
          <ol className="flex flex-col">
            {(Object.keys(NODE_META) as PipelineNodeId[]).map((id, i) => {
              const meta = NODE_META[id]
              return (
                <li
                  key={id}
                  className="flex items-center gap-3 border-b border-line py-2 last:border-b-0"
                >
                  <span className="data-text flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-2 text-xs text-ink-secondary">
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-ink">{meta.title}</p>
                    <p className="text-xs text-ink-secondary">{meta.desc}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="data-text text-sm text-ink">{state.counters[id] ?? 0}</p>
                    <p className="text-xs text-ink-secondary">{strings.pipeline.badge.passed}</p>
                  </div>
                </li>
              )
            })}
          </ol>
        </Card>

        <Card title={strings.pipeline.flow.recent} flush>
          {state.recent.length === 0 ? (
            <p className="px-4 py-3 text-sm text-ink-secondary">{strings.pipeline.empty}</p>
          ) : (
            <ul className="flex flex-col">
              {state.recent.map((event) => (
                <li
                  key={event.key}
                  className="flex items-start gap-2 border-b border-line px-3 py-1.5 last:border-b-0"
                >
                  <StatusDot tone={categoryTone[event.category]} size={7} />
                  <div className="min-w-0">
                    <p className="truncate text-sm text-ink" title={event.message}>
                      {event.message}
                    </p>
                    <p className="data-text text-xs text-ink-secondary">
                      {new Date(event.ts).toLocaleTimeString('zh-CN', { hour12: false })}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}
