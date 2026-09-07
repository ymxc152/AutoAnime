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
import { api } from '../api'
import { useApi } from '../hooks/useApi'
import { useEventStream } from '../hooks/eventStreamContext'
import { strings } from '../strings'
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  PageTitle,
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
  SseEvent,
} from '../api/types'

function PipelineNodeView({ data }: NodeProps<Node<PipelineNodeData>>) {
  const rate = data.entered > 0 ? Math.round((data.passed / data.entered) * 100) : null
  return (
    <div
      data-testid={`pipeline-node-${data.title}`}
      className={`w-40 rounded-md border bg-surface px-3 py-2 shadow-soft-sm ${
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


function ManualOperations() {
  const [parseName, setParseName] = useState('')
  const [preview, setPreview] = useState<ParsePreviewResponse | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [directory, setDirectory] = useState('')
  const [dryRun, setDryRun] = useState(true)
  const [task, setTask] = useState<PipelineTask | null>(null)
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)

  const [schedulerResult, setSchedulerResult] = useState<SchedulerRunResponse | null>(null)
  const [schedulerBusy, setSchedulerBusy] = useState(false)
  const [schedulerError, setSchedulerError] = useState<string | null>(null)

  const submitPreview = async (): Promise<void> => {
    if (parseName.trim() === '') return
    setPreviewBusy(true)
    setPreviewError(null)
    try {
      setPreview(await api.pipeline.parsePreview({ name: parseName.trim() }))
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
    if (directory.trim() === '') return
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
      setSchedulerResult(await api.scheduler.runOnce({ scope: 'all' }))
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
          <Field label={strings.pipeline.parseName} description={strings.pipeline.parseNameHint} htmlFor="pipeline-parse-name">
            <Input
              id="pipeline-parse-name"
              value={parseName}
              onChange={(e) => setParseName(e.target.value)}
              placeholder="Show S01E01 1080p.mkv"
            />
          </Field>
          <Button type="submit" variant="secondary" loading={previewBusy}>
            {strings.pipeline.parsePreview}
          </Button>
          {previewError !== null && <p role="alert" className="text-xs text-danger">{previewError}</p>}
          {preview !== null && (
            <pre className="data-text overflow-x-auto rounded-sm bg-surface-2 px-2 py-1.5 text-xs">
              {typeof preview === 'string' ? preview : JSON.stringify(preview.result, null, 2)}
            </pre>
          )}
        </form>

        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            void submitImport()
          }}
        >
          <Field label={strings.pipeline.importDirectory} htmlFor="pipeline-import-directory">
            <Input
              id="pipeline-import-directory"
              value={directory}
              onChange={(e) => setDirectory(e.target.value)}
              className="data-text"
            />
          </Field>
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-ink">{strings.pipeline.dryRun}</span>
            <Switch checked={dryRun} onChange={setDryRun} aria-label={strings.pipeline.dryRun} />
          </div>
          <Button type="submit" variant="primary" loading={importBusy}>
            {strings.pipeline.startImport}
          </Button>
          {importError !== null && <p role="alert" className="text-xs text-danger">{importError}</p>}
          {task !== null && (
            <p className="text-xs text-ink-secondary data-text">
              {task.status === 'running'
                ? strings.pipeline.running
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
          <Button variant="secondary" loading={schedulerBusy} onClick={() => void runOnce()}>
            {strings.pipeline.runOnce}
          </Button>
          {schedulerError !== null && <p role="alert" className="text-xs text-danger">{schedulerError}</p>}
          {schedulerResult !== null && (
            <pre className="data-text overflow-x-auto rounded-sm bg-surface-2 px-2 py-1.5 text-xs">
              {JSON.stringify(schedulerResult, null, 2)}
            </pre>
          )}
        </div>
      </div>
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
                  className="flex items-start gap-2 border-b border-line px-3 py-2 last:border-b-0"
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
