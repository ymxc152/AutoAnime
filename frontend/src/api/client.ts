/*
 * HTTP 客户端:同源 /api 起步(dev 走 vite proxy),统一错误与 token 头。
 * 认证(D6):AUTOANIME_API_TOKEN 非空时后端要求 X-API-Token 头;
 * token 由用户经 localStorage 注入(单用户本地工具,无登录页)。
 */

const TOKEN_STORAGE_KEY = 'autoanime-api-token'

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export function getApiToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setApiToken(token: string): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY)
    }
  } catch {
    /* 忽略存储不可用 */
  }
}

type QueryValue = string | number | boolean | undefined | null

function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

/** 默认请求超时:后端常规接口远低于此值;长调用(parse-preview/reparse)按调用点覆盖 */
export const DEFAULT_TIMEOUT_MS = 120_000

interface ComposedSignal {
  signal: AbortSignal
  /** 请求结束(成功/失败)后必须调用,清理定时器与外部 signal 监听 */
  dispose: () => void
}

/** 把外部 signal(若有)与超时定时器合流到一个 controller:任一触发即中断请求 */
function composeAbortSignal(external: AbortSignal | undefined, timeoutMs: number): ComposedSignal {
  const controller = new AbortController()
  const timeoutReason = new DOMException(`请求超时(${Math.round(timeoutMs / 1000)}s)`, 'TimeoutError')
  const timer = setTimeout(() => controller.abort(timeoutReason), timeoutMs)
  const onExternalAbort = (): void => controller.abort(external?.reason)
  if (external !== undefined) {
    if (external.aborted) onExternalAbort()
    else external.addEventListener('abort', onExternalAbort, { once: true })
  }
  return {
    signal: controller.signal,
    dispose: () => {
      clearTimeout(timer)
      external?.removeEventListener('abort', onExternalAbort)
    },
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, QueryValue>
  signal?: AbortSignal
  /** 单请求超时覆盖(ms);默认 DEFAULT_TIMEOUT_MS(120s),长调用传 300_000 */
  timeoutMs?: number
}

/** FastAPI 422 校验错误单条:pydantic v2 错误项 {loc, msg, type, ctx} */
interface FastapiErrorItem {
  loc?: unknown
  msg?: unknown
  type?: unknown
  ctx?: Record<string, unknown>
}

/** pydantic 常见校验 type → 人话(其余保留原文 msg) */
const VALIDATION_TYPE_MAP: Record<string, string> = {
  missing: '缺失',
  string_too_short: '过短',
}

/** 从 pydantic ctx 取数值上限/下限(gt/ge/lt/le 之一) */
function limitFromCtx(ctx: Record<string, unknown> | undefined): string {
  if (ctx !== undefined) {
    for (const key of ['ge', 'gt', 'le', 'lt']) {
      if (key in ctx) return String(ctx[key])
    }
  }
  return '?'
}

/** 单条校验错误 → 「字段校验失败:{字段}: {原因}」 */
function formatValidationItem(item: FastapiErrorItem): string {
  const loc =
    Array.isArray(item.loc) && item.loc.length > 0 ? String(item.loc[item.loc.length - 1]) : ''
  const msg = typeof item.msg === 'string' ? item.msg : ''
  switch (item.type) {
    case 'greater_than_equal':
    case 'less_than_equal':
    case 'greater_than':
    case 'less_than': {
      const op =
        item.type === 'greater_than_equal'
          ? '≥'
          : item.type === 'less_than_equal'
            ? '≤'
            : item.type === 'greater_than'
              ? '>'
              : '<'
      return `字段校验失败:${loc}: 需 ${op} ${limitFromCtx(item.ctx)}`
    }
    default: {
      const type = typeof item.type === 'string' ? item.type : undefined
      return `字段校验失败:${loc}: ${type !== undefined ? (VALIDATION_TYPE_MAP[type] ?? msg) : msg}`
    }
  }
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = getApiToken()
  if (token) {
    headers['X-API-Token'] = token
  }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  const composed = composeAbortSignal(options.signal, timeoutMs)
  let response: Response
  try {
    response = await fetch(path + buildQuery(options.query ?? {}), {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: composed.signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'TimeoutError') {
      throw new ApiError(0, `请求超时:后端 ${Math.round(timeoutMs / 1000)}s 内未响应`)
    }
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      throw cause
    }
    throw new ApiError(0, '网络不可达:后端未启动或连接失败')
  } finally {
    composed.dispose()
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    // FastAPI 错误体:{ "detail": string } 或 422 校验错误 { "detail": [{loc,msg,type,ctx}] }
    try {
      const data = (await response.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') {
        detail = data.detail
      } else if (Array.isArray(data.detail)) {
        detail = data.detail.map((item) => formatValidationItem(item as FastapiErrorItem)).join('; ')
      }
    } catch {
      /* 非 JSON 错误体,保留 statusText */
    }
    if (response.status === 401) {
      // 统一认证错误文案：任何页面拿到 ApiError.status=401 都能给出同一可操作提示。
      throw new ApiError(401, 'API Token 无效或缺失：请在设置页检查 Token')
    }
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}
