/*
 * Settings 配置中心(12-E)测试:七标签页 Tabs、跨组 dirty 圆点、
 * 保存 toast 按档位合并计数、整页 form 回车提交、数字字段范围校验禁保存、
 * 并发写冲突 409(清草稿 + reload + payload 携带 base_updated_at)、
 * 密钥留空不进 body/清除提交 null、白名单外字段不出现、
 * qbit-test 行内状态文本(成功含版本/失败原因,不再走 toast)且按钮保持 Button 形态、
 * notify-test 按钮结果 toast;环境页优先显示绝对路径(旧后端缺省回退配置值);
 * 参考源顺序(A1)与 API Token 本端注入(A3)回归。
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { SettingsPage } from '../Settings'
import { renderPage } from '../../test/testUtils'
import { api, ApiError } from '../../api'
import { createMockApi, MOCK_SETTINGS_UPDATED_AT, resetMockState } from '../../mocks/handlers'
import { mockSettings } from '../../mocks/data'

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

  it('dirty 逐值比对:改回原值/密钥删空/取消清除勾选不再算脏', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await screen.findByRole('switch', { name: '试运行模式' })
    // 开关拨开再拨回原位(基线 dry_run=false)→ 运行圆点消失、保存按钮回禁用
    await user.click(screen.getByRole('switch', { name: '试运行模式' }))
    expect(screen.getByLabelText('运行有未保存更改')).toBeInTheDocument()
    await user.click(screen.getByRole('switch', { name: '试运行模式' }))
    expect(screen.queryByLabelText('运行有未保存更改')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
    // 文本改回原值(基线 llm_model='deepseek-chat')→ 不脏
    await gotoTab(user, '识别')
    const model = await screen.findByLabelText('模型')
    await user.clear(model)
    await user.type(model, 'deepseek-chat')
    expect(screen.queryByLabelText('识别有未保存更改')).not.toBeInTheDocument()
    // 密钥输入后删空 → 不脏(空串 = 无输入意图)
    const keyInput = screen.getByLabelText('LLM API Key')
    await user.type(keyInput, 'sk-x')
    expect(screen.getByLabelText('识别有未保存更改')).toBeInTheDocument()
    await user.clear(keyInput)
    expect(screen.queryByLabelText('识别有未保存更改')).not.toBeInTheDocument()
    // 勾选清除 → 脏;取消勾选 → 不脏
    await user.click(screen.getByLabelText('清除已配置值:LLM API Key'))
    expect(screen.getByLabelText('识别有未保存更改')).toBeInTheDocument()
    await user.click(screen.getByLabelText('清除已配置值:LLM API Key'))
    expect(screen.queryByLabelText('识别有未保存更改')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('保存 toast 合并:单字段 immediate 档只弹一条 success(计数描述)', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await user.click(await screen.findByRole('switch', { name: '启用 LLM 兜底' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('已保存 1 项', { description: '1 项立即生效' }),
    )
  })

  it('保存 toast 合并:scheduler_rebuild 档计数进描述', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '调度')
    const interval = await screen.findByLabelText('RSS 轮询间隔(分钟)')
    await user.clear(interval)
    await user.type(interval, '30')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('已保存 1 项', { description: '1 项调度重建后生效' }),
    )
  })

  it('保存 toast 合并:requires_restart 档走 success 描述,不再弹 warning', async () => {
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
      expect(successSpy).toHaveBeenCalledWith('已保存 1 项', { description: '1 项重启后生效' }),
    )
    expect(warningSpy).not.toHaveBeenCalled()
    expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ qbittorrent_port: 8081 }))
    updateSpy.mockRestore()
  })

  it('保存 toast 洪水合并:跨三个档位各改一项,只弹一条 success + 一条描述', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    // immediate(运行开关)+ scheduler_rebuild(调度)+ requires_restart(端口)
    await user.click(await screen.findByRole('switch', { name: '试运行模式' }))
    await gotoTab(user, '调度')
    const interval = await screen.findByLabelText('RSS 轮询间隔(分钟)')
    await user.clear(interval)
    await user.type(interval, '30')
    await gotoTab(user, '下载器')
    const port = await screen.findByLabelText('qBittorrent 端口')
    await user.clear(port)
    await user.type(port, '8081')
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith('已保存 3 项', {
        description: '1 项立即生效 · 1 项调度重建后生效 · 1 项重启后生效',
      }),
    )
    expect(successSpy).toHaveBeenCalledTimes(1)
    expect(warningSpy).not.toHaveBeenCalled()
  })

  it('数字字段超范围:行内提示 + 保存禁用;修正后恢复(不拦截输入)', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '调度')
    const retries = await screen.findByLabelText('下载最大重试')
    await user.clear(retries)
    await user.type(retries, '99')
    expect(await screen.findByText('允许范围 0 ~ 10')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
    await user.clear(retries)
    await user.type(retries, '5')
    expect(screen.queryByText('允许范围 0 ~ 10')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
  })

  it('整页 form:输入框内回车触发保存,payload 携带 base_updated_at', async () => {
    const user = userEvent.setup()
    const updateSpy = vi.spyOn(api.settings, 'update')
    renderPage(<SettingsPage />)
    await gotoTab(user, '识别')
    const model = await screen.findByLabelText('模型')
    await user.type(model, '-v2{enter}')
    await waitFor(() => expect(updateSpy).toHaveBeenCalled())
    await waitSaved()
    expect(updateSpy.mock.calls[0]![0]).toMatchObject({
      llm_model: 'deepseek-chat-v2',
      base_updated_at: MOCK_SETTINGS_UPDATED_AT,
    })
    updateSpy.mockRestore()
  })

  it('并发写冲突 409:提示 settingsChangedElsewhere,清空草稿并以服务端为准 reload', async () => {
    const user = userEvent.setup()
    const updateSpy = vi
      .spyOn(api.settings, 'update')
      .mockRejectedValueOnce(new ApiError(409, 'settings_changed'))
    renderPage(<SettingsPage />)
    await user.click(await screen.findByRole('switch', { name: '试运行模式' }))
    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(errorSpy).toHaveBeenCalledWith('配置已被其它窗口修改,请刷新页面后再保存'),
    )
    // 草稿被清空:dirty 消失、保存按钮回禁用
    await waitFor(() => {
      expect(screen.queryByLabelText('运行有未保存更改')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
    })
    updateSpy.mockRestore()
  })

  it('mock update 409 路径:base_updated_at 不匹配回 settings_changed,成功后 updated_at 前移', async () => {
    const mockApi = createMockApi()
    resetMockState()
    await expect(
      mockApi.settings.update({ dry_run: true, base_updated_at: 'stale' }),
    ).rejects.toMatchObject({ status: 409, message: 'settings_changed' })
    const current = await mockApi.settings.get()
    expect(current.updated_at).toBe(MOCK_SETTINGS_UPDATED_AT)
    const out = await mockApi.settings.update({
      dry_run: true,
      base_updated_at: current.updated_at ?? null,
    })
    expect(out.updated_at).toBeTruthy()
    expect(out.updated_at).not.toBe(current.updated_at)
    // 成功保存后 GET 的基线已前移:再带旧基线提交 → 409
    await expect(
      mockApi.settings.update({ dry_run: false, base_updated_at: current.updated_at ?? null }),
    ).rejects.toMatchObject({ status: 409 })
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
    expect(payload).toEqual({ notify_enabled: true, base_updated_at: MOCK_SETTINGS_UPDATED_AT }) // 无白名单外字段混入(并发基线除外)
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

  it('qbit-test:行内状态展示成功(含版本)/失败原因,按钮保持 Button 形态且不产生 dirty', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '下载器')
    const btn = await screen.findByRole('button', { name: '测试连接' })
    await user.click(btn)
    // loading 态:请求未返回时按钮禁用,但仍为带边框的 Button 形态(不吞样式)
    expect(btn).toBeDisabled()
    expect(btn).toHaveClass('border-line')
    // 成功:行内状态文本展示(含版本号),不再走 toast(断言只排除 qbit 测试自己的
    // toast——相邻用例异步收尾的保存 toast 会晚到本用例的 spy,属既有时序噪声)
    expect(await screen.findByText('qBittorrent 连接成功(v2.0.9)')).toBeInTheDocument()
    expect(successSpy).not.toHaveBeenCalledWith(expect.stringContaining('qBittorrent 连接成功'))
    expect(btn).toBeEnabled()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled() // 测试动作不改 dirty
    // 失败分支:行内展示失败原因
    const qbitSpy = vi
      .spyOn(api.settings, 'qbitTest')
      .mockRejectedValueOnce(new ApiError(502, 'gateway unreachable'))
    await user.click(screen.getByRole('button', { name: '测试连接' }))
    expect(await screen.findByText('qBittorrent 连接失败: gateway unreachable')).toBeInTheDocument()
    expect(errorSpy).not.toHaveBeenCalledWith(expect.stringContaining('qBittorrent 连接失败'))
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

  it('只读环境信息(旧后端缺省 abs 字段回退显示配置值)与密钥状态徽标(环境页)', async () => {
    const user = userEvent.setup()
    renderPage(<SettingsPage />)
    await gotoTab(user, '环境')
    // mock 未返回 library_path_abs/download_path_abs → 回退显示 library_path/download_path
    expect(screen.getByText('/library')).toBeInTheDocument()
    expect(screen.getByText('/downloads')).toBeInTheDocument()
    expect(screen.queryByText(/配置值：/)).not.toBeInTheDocument()
    expect(screen.getByText('127.0.0.1:8000')).toBeInTheDocument()
    // mock 基线:API Token 未配置 → 徽标「未配置」
    expect(screen.getByText('未配置')).toBeInTheDocument()
  })

  it('环境页优先显示绝对路径,并以 muted 小字保留原配置值', async () => {
    const user = userEvent.setup()
    const getSpy = vi.spyOn(api.settings, 'get').mockResolvedValue({
      ...mockSettings,
      updated_at: MOCK_SETTINGS_UPDATED_AT,
      library_path_abs: '/data/media/library',
      download_path_abs: '/data/downloads',
    })
    renderPage(<SettingsPage />)
    await gotoTab(user, '环境')
    // 主值 = 后端 resolve 的绝对路径;原配置值以 muted 小字保留对照
    expect(screen.getByText('/data/media/library')).toBeInTheDocument()
    expect(screen.getByText('配置值：/library')).toBeInTheDocument()
    expect(screen.getByText('/data/downloads')).toBeInTheDocument()
    expect(screen.getByText('配置值：/downloads')).toBeInTheDocument()
    getSpy.mockRestore()
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
