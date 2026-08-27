import { extractApiFailure } from './errors'

const CSRF_KEY = 'autoanime.csrf'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: unknown = null) {
    super(message)
  }
}

export function setCsrfToken(token: string | null) {
  if (token) sessionStorage.setItem(CSRF_KEY, token)
  else sessionStorage.removeItem(CSRF_KEY)
}

export function hasCsrfToken(): boolean {
  return Boolean(sessionStorage.getItem(CSRF_KEY))
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const csrf = sessionStorage.getItem(CSRF_KEY)
  if (csrf && init.method && init.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const failure = extractApiFailure(body, response.status, response.statusText)
    throw new ApiError(response.status, failure.code, failure.message, failure.details)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const failure = extractApiFailure(body, response.status, response.statusText)
    throw new ApiError(response.status, failure.code, failure.message, failure.details)
  }
  return response.text()
}

export const api = {
  get: <T,>(path: string) => apiFetch<T>(path),
  post: <T,>(path: string, body?: unknown, headers?: HeadersInit) => apiFetch<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), headers }),
  put: <T,>(path: string, body: unknown) => apiFetch<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T,>(path: string, body: unknown) => apiFetch<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T,>(path: string, body?: unknown) => apiFetch<T>(path, { method: 'DELETE', body: body === undefined ? undefined : JSON.stringify(body) }),
  text: (path: string) => apiText(path),
}
