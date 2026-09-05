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

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, QueryValue>
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = getApiToken()
  if (token) {
    headers['X-API-Token'] = token
  }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  let response: Response
  try {
    response = await fetch(path + buildQuery(options.query ?? {}), {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      throw cause
    }
    throw new ApiError(0, '网络不可达:后端未启动或连接失败')
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    // FastAPI 错误体假设:{ "detail": string }
    try {
      const data = (await response.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') {
        detail = data.detail
      }
    } catch {
      /* 非 JSON 错误体,保留 statusText */
    }
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}
