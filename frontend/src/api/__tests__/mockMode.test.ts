/*
 * mock 开关缺省语义回归:裸跑 dev 不再默认假数据。
 * resolveUseMock 缺省 → false(连真实 API);仅显式 VITE_USE_MOCK=1 或
 * localStorage autoanime-use-mock='1' 才进 mock;=0 / '0' 强制真实。
 * isMockMode 是 api/index 的模块级常量(import 时求值),
 * 用 vi.resetModules + 动态 import 逐场景重载模块。
 * 注意:vitest.setup.ts 会全局把 localStorage 开关置 '1',
 * 因此每个用例先清掉它再按场景设置。
 */
async function loadIsMockMode(): Promise<boolean> {
  const mod = await import('../index')
  return mod.isMockMode
}

describe('resolveUseMock 缺省语义', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.removeItem('autoanime-use-mock')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    localStorage.removeItem('autoanime-use-mock')
  })

  it('缺省(无环境变量、无 localStorage)连真实 API,即使 vitest 下 DEV=true', async () => {
    vi.stubEnv('VITE_USE_MOCK', '')
    expect(await loadIsMockMode()).toBe(false)
  })

  it('VITE_USE_MOCK=1 强制 mock', async () => {
    vi.stubEnv('VITE_USE_MOCK', '1')
    expect(await loadIsMockMode()).toBe(true)
  })

  it("localStorage autoanime-use-mock='1' 运行时覆盖为 mock", async () => {
    vi.stubEnv('VITE_USE_MOCK', '')
    localStorage.setItem('autoanime-use-mock', '1')
    expect(await loadIsMockMode()).toBe(true)
  })

  it('VITE_USE_MOCK=0 优先于 localStorage=1,强制真实', async () => {
    vi.stubEnv('VITE_USE_MOCK', '0')
    localStorage.setItem('autoanime-use-mock', '1')
    expect(await loadIsMockMode()).toBe(false)
  })

  it("localStorage autoanime-use-mock='0' 强制真实", async () => {
    vi.stubEnv('VITE_USE_MOCK', '')
    localStorage.setItem('autoanime-use-mock', '0')
    expect(await loadIsMockMode()).toBe(false)
  })
})
