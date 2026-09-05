/*
 * 数据获取的统一 loading/error 态(Plan §5.2)。
 * 约定:fetcher 必须用 useCallback 记忆(依赖变化 = 重新拉取),
 * 变更操作后调用 reload() 刷新列表。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { isAbortError } from './async'

export interface UseApiResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

export function useApi<T>(fetcher: () => Promise<T>): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    // 状态更新全部发生在异步回调里(react-hooks/set-state-in-effect 纪律):
    // 首帧 loading=true 由初始值承担;refetch 时上一次结果保持展示,新结果到达后原子切换。
    void Promise.resolve()
      .then(() => fetcher())
      .then(
        (result) => {
          if (alive) {
            setData(result)
            setError(null)
            setLoading(false)
          }
        },
        (cause: unknown) => {
          if (alive && !isAbortError(cause)) {
            setError(cause instanceof Error ? cause.message : String(cause))
            setLoading(false)
          }
        },
      )
    return () => {
      alive = false
    }
  }, [fetcher, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])

  return useMemo(
    () => ({ data, loading, error, reload }),
    [data, loading, error, reload],
  )
}
