/*
 * Layout 单测:mock 模式下 main 内容区顶部渲染全宽警示横幅
 * (strings.app.mockBanner,warning 色调),且处于普通文档流、
 * 不遮挡页面内容;真实模式不渲染横幅。
 * isMockMode 是 api/index 的模块级常量,用 vi.doMock + 动态 import
 * 逐场景切换(不影响其他测试文件)。
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { FakeEventSource } from '../../test/testUtils'
import { strings } from '../../strings'

async function mountLayout(isMock: boolean) {
  vi.resetModules()
  vi.doMock('../../api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../api')>()
    return { ...actual, isMockMode: isMock }
  })
  // EventStreamProvider 必须与 Layout 在同一次 resetModules 后动态加载:
  // 否则两者拿到的 eventStreamContext 是不同模块实例,Provider 注不进 Consumer
  const [{ Layout }, { EventStreamProvider }] = await Promise.all([
    import('../Layout'),
    import('../../hooks/EventStreamProvider'),
  ])
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <EventStreamProvider factory={(url) => new FakeEventSource(url)}>
        <Layout>
          <div data-testid="page-content">页面内容</div>
        </Layout>
      </EventStreamProvider>
    </MemoryRouter>,
  )
}

describe('Layout mock 警示横幅', () => {
  afterEach(() => {
    vi.doUnmock('../../api')
  })

  it('mock 模式:顶部横幅展示 mockBanner 文案,且页面内容不受遮挡', async () => {
    await mountLayout(true)
    const banner = screen.getByTestId('mock-banner')
    expect(banner).toHaveTextContent(strings.app.mockBanner)
    expect(screen.getByTestId('page-content')).toBeVisible()
  })

  it('真实模式:不渲染横幅', async () => {
    await mountLayout(false)
    expect(screen.queryByTestId('mock-banner')).not.toBeInTheDocument()
  })
})
