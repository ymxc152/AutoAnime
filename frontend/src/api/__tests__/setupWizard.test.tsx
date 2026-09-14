/*
 * 首次运行设置向导(#/setup)mock 模式冒烟测试:
 * 1) 全流程走通:欢迎 → 连接(预填/测试) → 路径对齐 → 完成(complete 落标记)
 * 2) 路径不一致分支:pathsMismatch + 「采用 qB 路径」PUT download_path 后进完成步
 * 3) 步骤态 sessionStorage 持久化(刷新后仍停原步)与回退
 * 4) Layout 引导条:needed 时侧栏顶部 amber 入口 + 底部「重新运行向导」常驻入口
 * 走 vitest.setup 固定的 mock 模式(api = createMockApi,resetMockState 复位)。
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { SetupPage } from '../../pages/Setup'
import { Layout } from '../../components/Layout'
import { EventStreamProvider } from '../../hooks/EventStreamProvider'
import { FakeEventSource, renderPage } from '../../test/testUtils'
import { api } from '../../api'
import type { SetupStatusDto } from '../../api/types'
import { strings } from '../../strings'
import { resetMockState } from '../../mocks/handlers'

const alignedStatus: SetupStatusDto = {
  needed: true,
  has_subscription: false,
  downloader_configured: true,
  downloader_reachable: true,
  qb_save_path: 'C:/downloads',
  paths_aligned: true,
}

const mismatchedStatus: SetupStatusDto = {
  needed: true,
  has_subscription: false,
  downloader_configured: true,
  downloader_reachable: true,
  qb_save_path: 'D:/qb',
  paths_aligned: false,
}

function renderSetup(): void {
  // renderPage 提供内存 data router(SetupPage 用 useNavigate 跳转)
  renderPage(<SetupPage />)
}

function renderLayout(): void {
  render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <EventStreamProvider factory={(url) => new FakeEventSource(url)}>
        <Layout>
          <div data-testid="page-content">页面内容</div>
        </Layout>
      </EventStreamProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  resetMockState()
  sessionStorage.clear()
})

describe('SetupPage 向导动线', () => {
  it('全流程:欢迎 → 连接测试成功 → 路径对齐 → 完成并落 complete', async () => {
    const user = userEvent.setup()
    const completeSpy = vi.spyOn(api.setup, 'complete')
    renderSetup()

    // ① 欢迎:标题 + 三步说明 + 跳过向导入口
    expect(await screen.findByText(strings.setup.wizardTitle)).toBeVisible()
    expect(screen.getByText(strings.setup.welcomeBody)).toBeVisible()
    expect(screen.getByRole('button', { name: strings.setup.skipWizard })).toBeVisible()

    // 进入 ②(CTA 文案 = 下一步名,欢迎区内唯一)
    const welcome = screen.getByTestId('setup-step-welcome')
    await user.click(
      within(welcome).getByRole('button', { name: strings.setup.stepDownloader }),
    )
    const downloader = await screen.findByTestId('setup-step-downloader')
    expect(sessionStorage.getItem('autoanime-setup-step')).toBe('2')

    // 预填现有配置(mock settings:127.0.0.1 / 8080 / admin)
    const host = within(downloader).getByLabelText(strings.settings.qbHost) as HTMLInputElement
    await waitFor(() => expect(host).toHaveValue('127.0.0.1'))
    expect(within(downloader).getByLabelText(strings.settings.qbPort)).toHaveValue('8080')

    // ② 测试连接并继续(mock qbitTest ok:true + save_path)
    const updateSpy = vi.spyOn(api.settings, 'update')
    await user.click(
      within(downloader).getByRole('button', { name: strings.setup.testAndContinue }),
    )
    // 连接参数先落库(空密码不进 body = 不修改已有值),再按合并配置试跑
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ qbittorrent_host: '127.0.0.1', qbittorrent_port: 8080 }),
      ),
    )
    // ③ 路径对齐:mock status aligned → pathsOk 直接可继续
    const paths = await screen.findByTestId('setup-step-paths')
    expect(await within(paths).findByTestId('setup-paths-ok')).toBeVisible()
    expect(sessionStorage.getItem('autoanime-setup-step')).toBe('3')

    // ④ 完成:complete 调用 + doneBody + 去选番按钮
    await user.click(within(paths).getByRole('button', { name: strings.setup.stepDone }))
    const done = await screen.findByTestId('setup-step-done')
    expect(await within(done).findByTestId('setup-complete-ok')).toBeVisible()
    expect(within(done).getByText(strings.setup.doneBody)).toBeVisible()
    expect(within(done).getByRole('button', { name: strings.setup.goSubscribe })).toBeVisible()
    expect(completeSpy).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('autoanime-setup-step')).toBe('4')
  })

  it('路径不一致:mismatch 文案 + 采用 qB 路径(PUT download_path)后进完成步', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.setup, 'status').mockResolvedValue(mismatchedStatus)
    const updateSpy = vi.spyOn(api.settings, 'update')
    sessionStorage.setItem('autoanime-setup-step', '3') // 直接落到路径对齐步
    renderSetup()

    const paths = await screen.findByTestId('setup-step-paths')
    expect(within(paths).getByText(strings.setup.pathsMismatch)).toBeVisible()
    expect(within(paths).queryByTestId('setup-paths-ok')).not.toBeInTheDocument()

    // 「我去 qB 里改」给提示 + 「重试」重新检测,停留本步
    await user.click(within(paths).getByRole('button', { name: strings.setup.goToQb }))
    expect(within(paths).getByTestId('setup-go-to-qb-hint')).toBeVisible()
    await user.click(within(paths).getByRole('button', { name: strings.common.retry }))
    expect(sessionStorage.getItem('autoanime-setup-step')).toBe('3')

    // 「采用 qB 路径」:PUT download_path = qb_save_path(绝对路径)后进完成步
    await user.click(within(paths).getByRole('button', { name: strings.setup.adoptQbPath }))
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith({ download_path: 'D:/qb' }),
    )
    const done = await screen.findByTestId('setup-step-done')
    // 等 complete 落地再结束用例(避免 teardown 后 resolve 成 unhandled)
    expect(await within(done).findByTestId('setup-complete-ok')).toBeVisible()
    expect(sessionStorage.getItem('autoanime-setup-step')).toBe('4')
  })

  it('步骤态持久化与回退:刷新后停在原步,可回退到上一步', async () => {
    const user = userEvent.setup()
    vi.spyOn(api.setup, 'status').mockResolvedValue(alignedStatus)
    sessionStorage.setItem('autoanime-setup-step', '3')
    renderSetup()

    // 模拟刷新:重新挂载后仍停在第 3 步(不回到欢迎)
    const paths = await screen.findByTestId('setup-step-paths')
    expect(await within(paths).findByTestId('setup-paths-ok')).toBeVisible()

    // 回退:‹ 下载器连接 → 第 2 步
    await user.click(
      within(paths).getByRole('button', { name: `‹ ${strings.setup.stepDownloader}` }),
    )
    expect(await screen.findByTestId('setup-step-downloader')).toBeVisible()
    expect(sessionStorage.getItem('autoanime-setup-step')).toBe('2')
  })
})

describe('Layout 向导引导', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('needed(尚无订阅):侧栏顶部 amber 引导入口 + 底部重跑向导入口', async () => {
    vi.spyOn(api.setup, 'status').mockResolvedValue(alignedStatus)
    renderLayout()
    expect(await screen.findByTestId('setup-needed-entry')).toHaveTextContent(
      strings.setup.wizardNeededHint,
    )
    expect(screen.getByTestId('setup-rerun-entry')).toHaveTextContent(
      strings.setup.rerunWizard,
    )
  })

  it('不需要向导:不显示引导条,底部重跑入口仍常驻', async () => {
    renderLayout()
    await waitFor(() =>
      expect(screen.queryByTestId('setup-needed-entry')).not.toBeInTheDocument(),
    )
    expect(screen.getByTestId('setup-rerun-entry')).toBeVisible()
  })
})
