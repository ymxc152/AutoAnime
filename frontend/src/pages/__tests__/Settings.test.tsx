/*
 * Settings 配置中心(12-E)测试:七标签页 Tabs、跨组 dirty 圆点、
 * applied 三档 toast、密钥留空不进 body/清除提交 null、白名单外字段不出现、
 * qbit-test/notify-test 按钮结果 toast;参考源顺序(A1)与 API Token 本端注入(A3)回归。
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { SettingsPage } from '../Settings'
import { renderPage } from '../../test/testUtils'
import { api, ApiError } from '../../api'
import { resetMockState } from '../../mocks/handlers'

/** 切到指定标签页(先等初始加载渲染出 Tabs;dirty 圆点会追加 accessible name,故用 ^ 前缀正则) */
async function gotoTab(user: ReturnType<typeof userEvent.setup>, tab: string): Promise<void> {
  await screen.findByRole('tab', { name: /^运行/ })
  await user.click(screen.getByRole('tab', { name: new RegExp(`^${tab}`) }))
}

/** toast spy 组(类型经返回值推导,避免 MockInstance 泛型标注不匹配) */
function spyToasts() {
  return {
    success: vi.spyOn(toast, 'success'),
    warning: vi.spyOn(toast, 'warning'),
    error: vi.spyOn(toast, 'error'),
  }
}

/** 等待一次保存完整落地(mock update 有 120ms 延迟;完成批次里重置草稿与 saved 指示) */
async function waitSaved(): Promise<void> {
  await screen.findByText('已保存')
}

describe('SettingsPage', () => {
  let successSpy: ReturnType<typeof spyToasts>['success']
  let warningSpy: ReturnType<typeof spyToasts>['warning']
  let errorSpy: ReturnType<typeof spyToasts>['error']

  beforeEach(() => {
    resetMockState()
    localStorage.removeItem('autoanime-api-token')
    vi.restoreAllMocks()
    const spies = spyToasts()
    successSpy = spies.success
    warningSpy = spies.warning
    errorSpy = spies.error
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('渲染七个标签页,默认运行页含开关与日志级别', async () => {
    renderPage(<SettingsPage />)
    expect(await screen.findByRole('tab', { name: '运行' })).toBeInTheDocument()
    for (const tab of ['识别', '下载器', '洗版', '调度', '通知', '环境']) {
      expect(screen.getByRole('tab', { name: tab })).toBeInTheDocument()
    }
    expect(screen.getByRole('switch', { name: '试运行模式' })).toBeInTheDocument()
    expect(screen.getByLabelText('日志级别')).toHaveValue('INFO')
    // 初始未保存 → 按钮禁用
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('跨组 dirty 圆点:两个标签页各改一项,圆点分别出现,保存后全部清空', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await screen.findByRole('switch', { name: '试运行模式' })
    // 运行页改动 → 运行圆点
    expect(screen.queryByLabelText('运行有未保存更改')).not.toBeInTheDocument()
    await user.click(screen.getByRole('switch', { name: '试运行模式' }))
    expect(screen.getByLabelText('运行有未保存更改')).toBeInTheDocument()
    // 识别页改动 → 识别圆点(草稿共享,运行圆点仍在)
    await gotoTab(user, '识别')
    await user.type(await screen.findByLabelText('模型'), 'glm-4')
    expect(screen.getByLabelText('识别有未保存更改')).toBeInTheDocument()
    expect(screen.getByLabelText('运行有未保存更改')).toBeInTheDocument()
    // 保存 → 圆点全清(save 为异步,等待完成后断言),按钮回禁用
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => {
      expect(screen.queryByLabelText('运行有未保存更改')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('识别有未保存更改')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
    })
  })

  it('保存后 applied 逐字段 toast:immediate 分支(开关)', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await user.click(await screen.findByRole('switch', { name: '启用 LLM 兜底' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('启用 LLM 兜底 已生效'),
    )
  })

  it('保存后 applied 逐字段 toast:scheduler_rebuild 分支(调度字段)', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '调度')
    const interval = await screen.findByLabelText('RSS 轮询间隔(分钟)')
    await user.clear(interval)
    await user.type(interval, '30')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('RSS 轮询间隔(分钟) 已生效(调度已重建)'),
    )
  })

  it('保存后 applied 逐字段 toast:requires_restart 分支(连接字段)+ 数字过滤', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    await gotoTab(user, '下载器')
    const port = await screen.findByLabelText('qBittorrent 端口')
    await user.clear(port)
    await user.type(port, '8081') // 数字过滤:非数字不进值
    expect(port).toHaveValue('8081')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(warningSpy).toHaveBeenCalledWith('qBittorrent 端口 重启后生效'),
    )
    expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ qbittorrent_port: 8081 }))
    updateSpy.mockRestore()
  })

  it('密钥留空不进 body;勾选清除提交 null', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    await gotoTab(user, '识别')
    const keyInput = await screen.findByLabelText('LLM API Key')
    // has_llm_api_key=true → 占位提示「留空保持不变」
    expect(keyInput).toHaveAttribute('placeholder', '已配置,留空保持不变')
    // 1) 输入新值 → 提交明文
    await user.type(keyInput, 'sk-new-key')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ llm_api_key: 'sk-new-key' })),
    )
    await waitSaved() // 等 save 异步收尾(重置草稿)后再继续交互
    // 2) 留空且未勾清除 → 该字段不进 body(先改别的字段解锁保存)
    updateSpy.mockClear()
    await user.type(screen.getByLabelText('模型'), 'glm-4')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(updateSpy).toHaveBeenCalled())
    await waitSaved()
    expect(updateSpy.mock.calls[0]![0]).not.toHaveProperty('llm_api_key')
    // 3) 勾选清除 → 提交 null
    updateSpy.mockClear()
    await user.click(screen.getByLabelText('清除已配置值:LLM API Key'))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ llm_api_key: null })),
    )
    updateSpy.mockRestore()
  })

  it('白名单外字段不出现(notify_timeout_s/notify_events 不渲染不提交)', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    await gotoTab(user, '通知')
    expect(await screen.findByRole('button', { name: '发送测试通知' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/通知超时/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('switch', { name: '启用通知' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(updateSpy).toHaveBeenCalled())
    const payload = updateSpy.mock.calls[0]![0] as Record<string, unknown>
    expect(payload).toEqual({ notify_enabled: true }) // 无白名单外字段混入
    updateSpy.mockRestore()
  })

  it('洗版浮点字段允许小数点', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    await gotoTab(user, '洗版')
    const threshold = await screen.findByLabelText('洗版触发阈值')
    await user.clear(threshold)
    await user.type(threshold, '1.5')
    expect(threshold).toHaveValue('1.5')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ upgrade_threshold: 1.5 })),
    )
    updateSpy.mockRestore()
  })

  it('qbit-test:成功 toast + 不产生 dirty;失败走 error toast', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '下载器')
    const btn = await screen.findByRole('button', { name: '测试连接' })
    await user.click(btn)
    // loading 态:请求未返回时按钮禁用
    expect(btn).toBeDisabled()
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith(expect.stringContaining('qBittorrent 连接成功')),
    )
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled() // 测试动作不改 dirty
    // 失败分支:error toast 携带明细
    const qbitSpy = vi
      .spyOn(api.settings, 'qbitTest')
      .mockRejectedValueOnce(new ApiError(502, 'gateway unreachable'))
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('gateway unreachable'))
    qbitSpy.mockRestore()
  })

  it('notify-test:成功(逐通道)/空通道警告 toast', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '通知')
    // mock 初始未配置任何通道 → 警告
    await user.click(await screen.findByRole('button', { name: '发送测试通知' }))
    await waitFor(() => expect(warningSpy).toHaveBeenCalledWith('未配置任何通知通道'))
    // 配置后(.mock 两通道成功)→ 成功 toast 列出通道
    const notifySpy = vi.spyOn(api.settings, 'notifyTest').mockResolvedValueOnce({
      results: [
        { channel: 'webhook', ok: true, error: null },
        { channel: 'telegram', ok: true, error: null },
      ],
    })
    await user.click(screen.getByRole('button', { name: '发送测试通知' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('测试通知已发送(webhook, telegram)'),
    )
    notifySpy.mockRestore()
  })

  it('回归 A1:参考源顺序末尾敲逗号不被回显抹掉,blur 后归一化写回', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    await gotoTab(user, '识别')
    const order = await screen.findByLabelText('参考源顺序')
    expect(order).toHaveValue('bangumi,tmdb')
    await user.clear(order)
    await user.type(order, 'tmdb,bangumi,')
    expect(order).toHaveValue('tmdb,bangumi,')
    await user.tab()
    expect(order).toHaveValue('tmdb,bangumi')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ reference_order: ['tmdb', 'bangumi'] }),
      ),
    )
    await waitFor(() => expect(screen.getByLabelText('参考源顺序')).toHaveValue('tmdb,bangumi'))
    updateSpy.mockRestore()
  })

  it('只读环境信息与密钥状态徽标(环境页)', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '环境')
    expect(screen.getByText('/library')).toBeInTheDocument()
    expect(screen.getByText('127.0.0.1:8000')).toBeInTheDocument()
    // mock 基线:API Token 未配置 → 徽标「未配置」
    expect(screen.getByText('未配置')).toBeInTheDocument()
  })

  it('回归 A3:API Token 本端注入——保存写入 localStorage 并提示,清除移除', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '环境')
    const tokenInput = await screen.findByLabelText('API Token(本端注入)')
    expect(tokenInput).toHaveAttribute('type', 'password')
    expect(tokenInput).toHaveValue('')
    await user.type(tokenInput, 'sk-test-123')
    await user.click(screen.getByRole('button', { name: '保存 Token' }))
    expect(await screen.findByText('已保存,后续请求即时生效')).toBeInTheDocument()
    expect(localStorage.getItem('autoanime-api-token')).toBe('sk-test-123')
    await user.click(screen.getByRole('button', { name: '清除 Token' }))
    expect(await screen.findByText('已清除,后续请求即时生效')).toBeInTheDocument()
    expect(localStorage.getItem('autoanime-api-token')).toBeNull()
    localStorage.removeItem('autoanime-api-token')
  })
})
