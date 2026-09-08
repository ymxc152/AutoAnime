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

describe('request non-422 HTTP error mapping', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('500 且后端无字符串 detail → 映射为服务器内部错误人话', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('boom', { status: 500 })))
    const error = await request('/api/x').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).message).toBe('服务器内部错误，请稍后再试或查看日志页')
  })

  it('404 且后端无字符串 detail → 资源不存在', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('gone', { status: 404 })))
    const error = await request('/api/x').catch((cause: unknown) => cause)
    expect((error as ApiError).status).toBe(404)
    expect((error as ApiError).message).toBe('资源不存在')
  })

  it('409 且后端无字符串 detail → 冲突文案', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('conflict', { status: 409 })))
    const error = await request('/api/x').catch((cause: unknown) => cause)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).message).toBe('冲突：资源状态已变化')
  })

  it('未知状态码 503 → 保留原文但加中文前缀', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('down', { status: 503 })))
    const error = await request('/api/x').catch((cause: unknown) => cause)
    // jsdom 的 Response.statusText 为空串：只断言中文前缀 + 状态码在列。
    expect((error as ApiError).message.startsWith('请求失败:503')).toBe(true)
  })

  it('后端带字符串 detail（如 404 not found）→ 保留原文不覆盖', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: 'subscription 9 not found' }), { status: 404 })),
    )
    const error = await request('/api/x').catch((cause: unknown) => cause)
    expect((error as ApiError).message).toBe('subscription 9 not found')
  })

  it('fetch reject（非超时非 abort）→ 无法连接后端人话', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const error = await request('/api/x').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(0)
    expect((error as ApiError).message).toBe('无法连接后端，请确认服务已启动')
  })
})

describe('request 422 validation detail formatting', () => {
  function respondWith(detail: unknown): void {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail }), { status: 422 })),
    )
  }

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('数组 detail:大于等于校验 → 字段 + 需 ≥ 限值(pydantic ctx.ge)', async () => {
    respondWith([
      { type: 'greater_than_equal', loc: ['body', 'season'], msg: 'Input should be greater than or equal to 1', ctx: { ge: 1 } },
    ])
    const error = await request('/api/pipeline/confirm-name').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(422)
    expect((error as ApiError).message).toBe('字段校验失败:season: 需 ≥ 1')
  })

  it('数组 detail:小于等于校验 → 需 ≤ 限值', async () => {
    respondWith([
      { type: 'less_than_equal', loc: ['body', 'season'], msg: 'Input should be less than or equal to 1000', ctx: { le: 1000 } },
    ])
    const error = await request('/api/pipeline/confirm-name').catch((cause: unknown) => cause)
    expect((error as ApiError).message).toBe('字段校验失败:season: 需 ≤ 1000')
  })

  it('数组 detail:missing / string_too_short 映射为人话', async () => {
    respondWith([
      { type: 'missing', loc: ['body', 'name'], msg: 'Field required' },
      { type: 'string_too_short', loc: ['body', 'name'], msg: 'String should have at least 1 character', ctx: { min_length: 1 } },
    ])
    const error = await request('/api/pipeline/confirm-name').catch((cause: unknown) => cause)
    expect((error as ApiError).message).toBe('字段校验失败:name: 缺失; 字段校验失败:name: 过短')
  })

  it('数组 detail:未知 type 保留 pydantic 原文 msg', async () => {
    respondWith([
      { type: 'value_error', loc: ['body', 'directory'], msg: 'value is not a valid directory' },
    ])
    const error = await request('/api/pipeline/import').catch((cause: unknown) => cause)
    expect((error as ApiError).message).toBe('字段校验失败:directory: value is not a valid directory')
  })

  it('字符串 detail 仍按原文展示(回归)', async () => {
    respondWith('directory must be an absolute path')
    const error = await request('/api/pipeline/import').catch((cause: unknown) => cause)
    expect((error as ApiError).message).toBe('directory must be an absolute path')
  })
})
