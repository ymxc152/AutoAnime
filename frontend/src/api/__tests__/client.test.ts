import { ApiError, request } from '../client'

/** 永不完成的 fetch:请求被 abort 时以 signal.reason reject(对齐真实 fetch 行为) */
function hangFetch(): ReturnType<typeof vi.fn> {
  return vi.fn((_path: string, init: RequestInit) => {
    const signal = init.signal as AbortSignal
    return new Promise<Response>((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason))
    })
  })
}

describe('request timeout', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('默认 120s 超时 → ApiError(0) 且消息含超时', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', hangFetch())
    const pending = request('/api/slow').catch((cause: unknown) => cause)
    await vi.advanceTimersByTimeAsync(120_000)
    const error = await pending
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(0)
    expect((error as ApiError).message).toContain('超时')
  })

  it('timeoutMs 单请求覆盖:50ms 即中断,无需等默认 120s', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', hangFetch())
    const pending = request('/api/slow', { timeoutMs: 50 }).catch((cause: unknown) => cause)
    await vi.advanceTimersByTimeAsync(50)
    const error = await pending
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(0)
    expect((error as ApiError).message).toContain('超时')
  })
})

describe('request auth handling', () => {
  it('normalizes 401 to the same actionable API error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'ignored backend detail' }), { status: 401 })))
    try {
      const error = await request('/api/metrics').catch((cause: unknown) => cause)
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(401)
      expect((error as ApiError).message).toBe('API Token 无效或缺失：请在设置页检查 Token')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
