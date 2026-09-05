/*
 * Pipeline 页 —— @xyflow/react 三级识别管线节点图(L1→L2→L3→arbiter→organize)。
 * 每节点:实时命中/通过徽标(基线来自 GET /api/metrics.levels + SSE 事件累加);
 * SSE 驱动文件流:事件到达后沿路径逐段点亮边(xyflow animated edge),
 * 侧栏展示最近事件流。流量模型见 pipelineFlow.ts(纯 reducer)。
 */
import { useCallback, useEffect, useMemo, useReducer } from 'react'
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
import { Badge, Button, Card, PageTitle, StatusDot } from '../components'
import {
  activeEdgesOf,
  flowReducer,
  initialFlowState,
  passingNodes,
  NODE_META,
  EDGE_DEFS,
  type PipelineNodeData,
  type PipelineNodeId,
} from './pipelineFlow'
import type { SseEvent } from '../api/types'

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
      dispatch({ type: 'seed', levels: metrics.levels })
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

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_280px]">
        <Card flush className="overflow-hidden">
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
