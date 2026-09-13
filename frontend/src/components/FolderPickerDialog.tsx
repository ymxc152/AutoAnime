/*
 * 目录选择弹窗(P1-D):GET /api/filesystem 逐级下钻,「选择当前目录」回填导入框。
 * 纯只读浏览;文案复用 strings.uxfix.picker* 与 strings.common.*。
 * 打开时从盘符根视图(path='')开始;行点击下钻,面包屑/上一级回跳。
 * 实现注:open 时才挂载 PickerContent —— 每次打开天然回到盘符根视图,
 * 且加载 effect 内不含同步 setState(lint react-hooks/set-state-in-effect)。
 *
 * P0 逃生门(2026-09-09):冷会话首开间歇性永久「加载中」排查结论 ——
 * 复现(playwright 全新 context 直开 #/pipeline 点「选择目录」)显示请求已发出但
 * 永不 finish,而受控链路(独立 vite + 日志代理 → 后端)8/8 全部 3ms 响应,
 * 挂起点不在本组件与后端,而在 vite dev proxy → 后端一段:后端不可达/未就绪时
 * http-proxy 对连接失败不回错而是悬挂,浏览器侧 fetch 永不 resolve(后端就绪后
 * 恢复正常,与「取消重开或预热后正常」一致)。api.filesystem.list 契约只收 path、
 * 无法注入 AbortSignal,故在此做 8s Promise 竞速逃生门:超时转既有错误+重试 UI,
 * 底层 fetch 仍由 client 的 120s 超时兜底回收;用 seq 守卫丢弃超时后才返回的
 * 旧响应,避免陈旧数据覆盖重试结果。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { api } from '../api'
import type { FilesystemListing } from '../api/types'
import { strings } from '../strings'
import { Button } from './Button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog'

interface BreadcrumbSegment {
  label: string
  target: string
}

/** path → 可点击面包屑段(兼容 \\ 与 / 两种分隔;盘符段补全尾反斜杠) */
function breadcrumbSegments(path: string): BreadcrumbSegment[] {
  const sep = path.includes('\\') ? '\\' : '/'
  const parts = path.split(/[\\/]+/).filter((part) => part !== '')
  const segments: BreadcrumbSegment[] = []
  let acc = ''
  for (const part of parts) {
    if (acc === '' && /^[A-Za-z]:$/.test(part)) {
      acc = `${part}\\`
    } else if (acc === '' && path.startsWith('/')) {
      acc = `/${part}`
    } else if (acc === '') {
      acc = part
    } else {
      acc = `${acc}${sep}${part}`
    }
    segments.push({ label: part, target: acc })
  }
  return segments
}

/** 当前目录 + 子目录名 → 子目录完整路径(盘符根视图下子项已是完整路径) */
function joinChild(currentPath: string, name: string): string {
  if (currentPath === '') return name
  const sep = currentPath.includes('\\') ? '\\' : '/'
  return currentPath.endsWith('/') || currentPath.endsWith('\\')
    ? `${currentPath}${name}`
    : `${currentPath}${sep}${name}`
}

export interface FolderPickerDialogProps {
  open: boolean
  onClose: () => void
  /** 选中「当前目录」回调(盘符根视图下不可选,path 为空不回调) */
  onPick: (path: string) => void
  /** 'file' 模式:列出文件,点击文件回调 onPickFile(完整路径) */
  mode?: 'directory' | 'file'
  onPickFile?: (path: string) => void
}

/** P0 逃生门:加载超过该时长仍无响应即转可重试错误态(见文件头排查结论) */
const PICKER_TIMEOUT_MS = 8_000

/** 给请求 Promise 挂 8s 超时竞速;任一方先落定即清理对方计时器 */
function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(strings.common.loadFailed)), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

export function FolderPickerDialog({
  open,
  onClose,
  onPick,
  mode = 'directory',
  onPickFile,
}: FolderPickerDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      {open && <PickerContent onClose={onClose} onPick={onPick} mode={mode} onPickFile={onPickFile} />}
    </Dialog>
  )
}

function PickerContent({
  onClose,
  onPick,
  mode,
  onPickFile,
}: {
  onClose: () => void
  onPick: (path: string) => void
  mode: 'directory' | 'file'
  onPickFile?: (path: string) => void
}) {
  const fileMode = mode === 'file' && onPickFile !== undefined
  const [currentPath, setCurrentPath] = useState('')
  const [listing, setListing] = useState<FilesystemListing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // P0 逃生门配套:请求代际号,超时/切换目录后丢弃旧响应,防止陈旧结果回写
  const seqRef = useRef(0)

  // 只在 await 之后 setState(初始 loading=true 由 useState 初值给出),
  // 触发加载前的 loading/error 置位在事件处理器里完成。
  const fetchListing = useCallback(async (path: string): Promise<void> => {
    const seq = seqRef.current + 1
    seqRef.current = seq
    try {
      // 目录模式保持单参调用(旧断言/旧行为不变);文件模式才带 include_files
      const result = await withTimeout(
        fileMode ? api.filesystem.list(path, true) : api.filesystem.list(path),
        PICKER_TIMEOUT_MS,
      )
      if (seqRef.current !== seq) return
      setListing(result)
      setCurrentPath(path)
      setError(null)
      setLoading(false)
    } catch (cause) {
      if (seqRef.current !== seq) return
      setError(cause instanceof Error ? cause.message : strings.common.actionFailed)
      setLoading(false)
    }
  }, [])

  // 挂载即从盘符根视图加载(PickerContent 仅在 open 时挂载);
  // setState 全部落在 promise 回调里(effect 不做同步 setState,故不直接调用
  // fetchListing);卸载时推进代际号,在途响应一律作废。
  useEffect(() => {
    const seq = seqRef.current + 1
    seqRef.current = seq
    withTimeout(fileMode ? api.filesystem.list('', true) : api.filesystem.list(''), PICKER_TIMEOUT_MS)
      .then((result) => {
        if (seqRef.current !== seq) return
        setListing(result)
        setCurrentPath('')
        setError(null)
        setLoading(false)
      })
      .catch((cause: unknown) => {
        if (seqRef.current !== seq) return
        setError(cause instanceof Error ? cause.message : strings.common.actionFailed)
        setLoading(false)
      })
    return () => {
      seqRef.current += 1
    }
  }, [])

  const navigate = (path: string): void => {
    setLoading(true)
    setError(null)
    void fetchListing(path)
  }

  const atDrivesRoot = currentPath === ''

  return (
    <DialogContent className="max-w-lg" data-testid="folder-picker-dialog">
      <DialogHeader>
        <DialogTitle>
          {fileMode ? strings.uxfix.pickerTitleFile : strings.uxfix.pickerTitle}
        </DialogTitle>
      </DialogHeader>

      <div className="flex min-h-0 flex-col gap-2">
        {/* 面包屑:根视图显示「此电脑」;每段可点击跳回 */}
        <div className="flex flex-wrap items-center gap-1 text-xs" data-testid="picker-breadcrumb">
          <button
            type="button"
            className={`rounded-sm px-1 py-0.5 ${atDrivesRoot ? 'bg-surface-2 text-ink' : 'text-ink-secondary hover:text-ink hover:bg-surface-2'}`}
            onClick={() => navigate('')}
          >
            {strings.uxfix.pickerDrives}
          </button>
          {breadcrumbSegments(currentPath).map((segment, index, all) => (
            <span key={segment.target} className="flex items-center gap-1">
              <span className="text-ink-secondary">/</span>
              <button
                type="button"
                className={`rounded-sm px-1 py-0.5 ${index === all.length - 1 ? 'bg-surface-2 text-ink' : 'text-ink-secondary hover:text-ink hover:bg-surface-2'}`}
                onClick={() => navigate(segment.target)}
              >
                {segment.label}
              </button>
            </span>
          ))}
        </div>

        {/* 上一级:根视图或已到顶层(parent null)禁用 */}
        <div className="flex items-center justify-between gap-2">
          <Button
            size="sm"
            variant="ghost"
            aria-label={strings.error.back}
            title={strings.error.back}
            disabled={atDrivesRoot || listing === null || listing.parent === null}
            onClick={() => {
              if (listing?.parent) navigate(listing.parent)
            }}
          >
            <ArrowUp aria-hidden className="h-3.5 w-3.5" />
          </Button>
          <span
            className="data-text min-w-0 flex-1 truncate text-right text-xs text-ink-secondary"
            title={currentPath}
          >
            {currentPath === '' ? strings.uxfix.pickerDrives : currentPath}
          </span>
        </div>

        <div className="max-h-72 min-h-24 overflow-y-auto rounded-sm border border-line">
          {loading ? (
            <p className="px-3 py-4 text-sm text-ink-secondary">{strings.common.loading}</p>
          ) : error !== null ? (
            <div className="flex items-center justify-between gap-2 px-3 py-3">
              <p role="alert" className="text-sm font-medium text-danger">{error}</p>
              <Button size="sm" variant="secondary" onClick={() => navigate(currentPath)}>
                {strings.common.retry}
              </Button>
            </div>
          ) : listing !== null && listing.directories.length === 0 && (listing.files?.length ?? 0) === 0 ? (
            <p className="px-3 py-4 text-sm text-ink-secondary">
              {fileMode ? strings.uxfix.pickerFileEmpty : strings.uxfix.pickerEmpty}
            </p>
          ) : (
            <ul className="flex flex-col">
              {listing?.directories.map((name) => (
                <li key={`dir-${name}`}>
                  <button
                    type="button"
                    data-testid={`picker-row-${name}`}
                    className="w-full truncate rounded-sm px-3 py-1.5 text-left text-sm text-ink hover:bg-surface-2"
                    onClick={() => navigate(joinChild(currentPath, name))}
                  >
                    {name}
                  </button>
                </li>
              ))}
              {fileMode &&
                (listing?.files ?? []).map((name) => (
                  <li key={`file-${name}`}>
                    <button
                      type="button"
                      data-testid={`picker-file-${name}`}
                      className="w-full truncate rounded-sm bg-surface-2/40 px-3 py-1.5 text-left text-sm text-ink hover:bg-surface-2"
                      onClick={() => onPickFile?.(joinChild(currentPath, name))}
                    >
                      {name}
                    </button>
                  </li>
                ))}
            </ul>
          )}
        </div>
      </div>

      <DialogFooter className="gap-2">
        <Button variant="ghost" onClick={onClose}>
          {strings.common.cancel}
        </Button>
        <Button
          variant="primary"
          disabled={atDrivesRoot || loading || error !== null}
          onClick={() => onPick(currentPath)}
        >
          {!fileMode && strings.uxfix.pickerChoose}
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
