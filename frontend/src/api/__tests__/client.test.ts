import { ApiError, request } from '../client'

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
