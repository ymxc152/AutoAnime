/*
 * Pipeline 页渲染冒烟 + SSE(mock 事件源)驱动的文件流动画集成:
 * 事件注入 → 最近事件列表更新 → token 推进 → organize 节点计数累加。
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PipelinePage } from '../Pipeline'
import { api, ApiError } from '../../api'
import { toast } from 'sonner'
import { FakeEventSource, renderPage, sseMessage } from '../../test/testUtils'
import { resetMockState } from '../../mocks/handlers'
import type { EventSourceFactory } from '../../api/sse'

describe('PipelinePage', () => {
  let registry: FakeEventSource[]

  beforeEach(() => {
    resetMockState()
    registry = []
  })

  function controlledFactory(): EventSourceFactory {
    return (url) => {
      const source = new FakeEventSource(url)
      registry.push(source)
      return source
    }
  }

  function emit(payload: Record<string, unknown>, category = 'parse', message = '测试事件'): void {
    const source = registry[registry.length - 1]
    act(() => {
      source?.emit(sseMessage({ id: String(Math.random()), category, message, payload }))
    })
  }

  it('渲染 7 个管线节点与命中率徽标', async () => {
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    expect(await screen.findByTestId('pipeline-node-L1 本地解析')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-L2 规则记忆')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-L3 LLM 兜底')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-仲裁')).toBeInTheDocument()
    expect(screen.getByTestId('pipeline-node-归档')).toBeInTheDocument()
    // 基线命中率来自 /api/metrics
    await waitFor(() => {
      expect(within(screen.getByTestId('pipeline-node-L1 本地解析')).getByText(/100%/)).toBeInTheDocument()
    })
  })

  it('SSE parse 事件驱动:最近事件列表出现,token 推进后 organize 计数 +1', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderPage(<PipelinePage />, { factory: controlledFactory() })
      // 等 metrics 基线就绪(organize 初始计数 = l1_high + l2_hit = 387)
      await screen.findByTestId('pipeline-node-归档')
      // 等 metrics 基线就绪(organize 初始计数 = l1_high + l2_hit = 387)
      await waitFor(() => {
        expect(Number(screen.getByTestId('node-count-归档').textContent)).toBe(387)
      })
      const before = Number(screen.getByTestId('node-count-归档').textContent)

      emit({ level: 1, outcome: 'l1_high', raw_name: 'demo.mkv' })
      // 最近事件列表出现该消息
      expect(await screen.findByText('测试事件')).toBeInTheDocument()
      // 推进 tick 直至 token 抵达终点(4 段路径)
      for (let i = 0; i < 4; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(900)
        })
      }
      expect(Number(screen.getByTestId('node-count-归档').textContent)).toBe(before + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('低置信事件最终落到人工确认节点', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderPage(<PipelinePage />, { factory: controlledFactory() })
      // pending 节点基线计数为 0;等 metrics 基线落到 L1 节点后再取 pending 当前值
      await screen.findByTestId('pipeline-node-L1 本地解析')
      // 等 metrics 基线就绪
      await waitFor(() => {
        expect(Number(screen.getByTestId('node-count-L1 本地解析').textContent)).toBe(431)
      })
      const before = Number(screen.getByTestId('node-count-人工确认').textContent)

      emit({ level: 1, outcome: 'low_confidence', confidence: 'low' })
      // path: input→l1→l2→arbiter→pending,4 段需要 4 次推进
      for (let i = 0; i < 5; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(900)
        })
      }
      expect(Number(screen.getByTestId('node-count-人工确认').textContent)).toBe(before + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('清空记录按钮重置最近事件列表', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      renderPage(<PipelinePage />, { factory: controlledFactory() })
      await screen.findByTestId('pipeline-node-归档')
      emit({ level: 1, outcome: 'l1_high' })
      expect(await screen.findByText('测试事件')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '清空记录' }))
      expect(screen.getByText('等待第一个文件进入管线…')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
  it('移动端降级:渲染纵向流程步骤列表(7 个节点)', async () => {
    renderPage(<PipelinePage />)
    expect(await screen.findByText('流程步骤')).toBeInTheDocument()
    // 步骤列表的节点标题(NODE_META 7 个)
    expect(screen.getAllByText('L1 本地解析').length).toBeGreaterThan(0)
    expect(screen.getAllByText('仲裁').length).toBeGreaterThan(0)
    // 步骤列表传 li 序号 1..7(序号徽标)
    expect(screen.getByText('7')).toBeInTheDocument()
  })

  it('手动解析试跑调用 pipeline API 并展示结果', async () => {
    const parsePreviewSpy = vi.spyOn(api.pipeline, 'parsePreview').mockResolvedValueOnce({
      route: 'archive',
      result: {
        title: 'Show', season: 1, episode: 1, segment: 'episode', fansub: null,
        level: 'high', confidence: 1, missing_fields: [], evidence: {},
      },
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('单文件解析试跑'), { target: { value: 'Show S01E01 1080p.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: '试跑解析' }))
    await waitFor(() => expect(parsePreviewSpy).toHaveBeenCalledWith({ name: 'Show S01E01 1080p.mkv' }))
      })

  it('单文件试跑可选填 folder/parent,填了才传、留空不传(12-F)', async () => {
    const parsePreviewSpy = vi.spyOn(api.pipeline, 'parsePreview').mockResolvedValueOnce({
      route: 'archive',
      result: {
        title: 'Show', season: 1, episode: 1, segment: 'episode', fansub: null,
        level: 'high', confidence: 1, missing_fields: [], evidence: {},
      },
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('单文件解析试跑'), { target: { value: 'Show S01E01 1080p.mkv' } })
    // 只填 folder:body 带 folder 不带 parent
    fireEvent.change(screen.getByLabelText('所在目录(可选)'), { target: { value: 'Season 1' } })
    fireEvent.click(screen.getByRole('button', { name: '试跑解析' }))
    await waitFor(() =>
      expect(parsePreviewSpy).toHaveBeenCalledWith({ name: 'Show S01E01 1080p.mkv', folder: 'Season 1' }),
    )
    // 再填 parent:两者都传
    fireEvent.change(screen.getByLabelText('父目录路径(可选)'), {
      target: { value: 'D:/downloads/Show/Season 1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '试跑解析' }))
    await waitFor(() =>
      expect(parsePreviewSpy).toHaveBeenLastCalledWith({
        name: 'Show S01E01 1080p.mkv',
        folder: 'Season 1',
        parent: 'D:/downloads/Show/Season 1',
      }),
    )
  })

  it('异步导入展示任务进度和完成摘要', async () => {
    vi.spyOn(api.pipeline, 'startImport').mockResolvedValueOnce({ task_id: 'task-1', status: 'running' })
    vi.spyOn(api.pipeline, 'task').mockResolvedValueOnce({
      task_id: 'task-1', kind: 'import', status: 'completed', directory: 'D:/downloads',
      dry_run: true, created_at: '', finished_at: '', processed: 1, total: 1,
      summary: { total: 1, scanned: 1, archived: 1, pending: 0, failed: 0, skipped: 0 },
      error: null,
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('导入目录'), { target: { value: 'D:/downloads' } })
    fireEvent.click(screen.getByRole('button', { name: '开始导入' }))
    expect(await screen.findByText(/已完成 · 1\/1/)).toBeInTheDocument()
  })

  it('手动跑一轮订阅闭环展示后端报告', async () => {
    const runOnceSpy = vi.spyOn(api.scheduler, 'runOnce').mockResolvedValueOnce({
      scope: 'all', reports: { rss: { picked: 0, gaps: 0, errors: [] } }, errors: [],
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.click(screen.getByRole('button', { name: '跑一轮订阅闭环' }))
    await waitFor(() => expect(runOnceSpy).toHaveBeenCalledWith({ scope: 'all' }))
  })

  it('run-once scope 三选:选「仅 RSS 轮询」后按 scope=rss 传参(12-F)', async () => {
    const runOnceSpy = vi.spyOn(api.scheduler, 'runOnce').mockResolvedValueOnce({
      scope: 'rss', reports: { rss: { picked: 0, gaps: 0, errors: [] } }, errors: [],
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    // 默认 all;切换为仅 RSS 轮询后执行
    fireEvent.change(screen.getByLabelText('执行范围'), { target: { value: 'rss' } })
    fireEvent.click(screen.getByRole('button', { name: '跑一轮订阅闭环' }))
    await waitFor(() => expect(runOnceSpy).toHaveBeenCalledWith({ scope: 'rss' }))
  })

  it('12-F:人工确认命名 —— 空文件名不提交;填写后按可选覆写提交并 toast 学习/归档结果', async () => {
    const successSpy = vi.spyOn(toast, 'success')
    const confirmNameSpy = vi.spyOn(api.pipeline, 'confirmName').mockResolvedValueOnce({
      bypassed: false,
      resolved_pending: 0,
      archive: {
        archived: true,
        dst: '/library/Show/Season 1/Show - S01E01.1080p.mkv',
        strategy: 'hardlink',
      },
      entries: [
        { key_level: 'show', key_hash: 'h1', title_shape: 'Show', source: 'manual', status: 'active', hit_count: 0, corrected_count: 0 },
        { key_level: 'episode', key_hash: 'h2', title_shape: 'Show', source: 'manual', status: 'active', hit_count: 0, corrected_count: 0 },
      ],
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    // 空文件名提交被前端校验拦截,不调后端
    fireEvent.click(screen.getByRole('button', { name: '确认并学习' }))
    expect(confirmNameSpy).not.toHaveBeenCalled()
    expect(await screen.findByText('请填写文件名')).toBeInTheDocument()
    // 填写文件名 + 可选覆写(标题留空不传、季/集/段落覆写)
    fireEvent.change(screen.getByLabelText('文件名'), { target: { value: 'Show S01E01 1080p.mkv' } })
    fireEvent.change(screen.getByLabelText('季'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('集'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('段落类型'), { target: { value: 'episode' } })
    fireEvent.click(screen.getByRole('button', { name: '确认并学习' }))
    await waitFor(() =>
      expect(confirmNameSpy).toHaveBeenCalledWith({
        name: 'Show S01E01 1080p.mkv',
        season: 1,
        episode: 1,
        segment: 'episode',
      }),
    )
    // toast = 学习结果(写入 2 条记忆) + 是否已归档(按响应字段组织)
    await waitFor(() =>
      expect(successSpy).toHaveBeenCalledWith(
        '已写入识别记忆 2 条,下次同类命名直接命中 · 已归档到 /library/Show/Season 1/Show - S01E01.1080p.mkv',
      ),
    )
    confirmNameSpy.mockRestore()
    successSpy.mockRestore()
  })

  it('12-F:人工确认命名 422 展示后端原因且不关闭表单', async () => {
    vi.spyOn(api.pipeline, 'confirmName').mockRejectedValueOnce(
      new ApiError(422, 'name resolved to an empty title'),
    )
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('文件名'), { target: { value: '???.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: '确认并学习' }))
    expect(await screen.findByText('name resolved to an empty title')).toBeInTheDocument()
  })

  it('UXfix:试跑文件名为空时行内提示且不调用后端', async () => {
    const parsePreviewSpy = vi.spyOn(api.pipeline, 'parsePreview')
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.click(screen.getByRole('button', { name: '试跑解析' }))
    expect(await screen.findByText('请先输入要试跑的文件名')).toBeInTheDocument()
    expect(parsePreviewSpy).not.toHaveBeenCalled()
  })

  it('UXfix:导入目录为空时行内提示且不调用后端', async () => {
    const startImportSpy = vi.spyOn(api.pipeline, 'startImport')
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.click(screen.getByRole('button', { name: '开始导入' }))
    expect(await screen.findByText('请先输入要导入的目录路径')).toBeInTheDocument()
    expect(startImportSpy).not.toHaveBeenCalled()
  })

  it('UXfix:试跑结果展示结构化中文卡片,原始 JSON 默认折叠', async () => {
    vi.spyOn(api.pipeline, 'parsePreview').mockResolvedValueOnce({
      route: 'archive',
      result: {
        title: 'Show', season: 1, episode: 1, segment: 'episode', fansub: 'Kamigakari',
        level: 'high', confidence: 1, missing_fields: [], evidence: {},
      },
    })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('单文件解析试跑'), { target: { value: 'Show S01E01 1080p.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: '试跑解析' }))
    const card = await screen.findByTestId('preview-result-card')
    expect(card).toHaveTextContent('试跑结果')
    expect(card).toHaveTextContent('Show')
    expect(card).toHaveTextContent('单集')
    expect(card).toHaveTextContent('Kamigakari')
    expect(card).toHaveTextContent('HIGH:自动归档')
    // 原始 JSON 默认收起,展开后可见(dump 的是 result 本体,不含外层 route)
    expect(within(card).getByText(/"title": "Show"/)).not.toBeVisible()
    fireEvent.click(within(card).getByText('查看原始 JSON'))
    await waitFor(() => expect(within(card).getByText(/"title": "Show"/)).toBeVisible())
  })

  it('UXfix:试跑解析不出字段时显示空结果提示', async () => {
    vi.spyOn(api.pipeline, 'parsePreview').mockResolvedValueOnce({ route: 'failed', result: null })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('单文件解析试跑'), { target: { value: '???.mkv' } })
    fireEvent.click(screen.getByRole('button', { name: '试跑解析' }))
    expect(
      await screen.findByText('未能解析出有效字段;这类命名建议在「待确认」页人工纠正后学习'),
    ).toBeInTheDocument()
  })

  it('UXfix:导入运行中轮询到 processed/total 时显示已处理进度', async () => {
    vi.spyOn(api.pipeline, 'startImport').mockResolvedValueOnce({ task_id: 'task-2', status: 'running' })
    // 第二次轮询永不返回:保持 running 态以便断言进度文案
    vi.spyOn(api.pipeline, 'task')
      .mockResolvedValueOnce({
        task_id: 'task-2', kind: 'import', status: 'running', directory: 'D:/downloads',
        dry_run: true, created_at: '', finished_at: null, processed: 1, total: 3,
        summary: null, error: null,
      })
      .mockReturnValueOnce(new Promise(() => {}))
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    fireEvent.change(screen.getByLabelText('导入目录'), { target: { value: 'D:/downloads' } })
    fireEvent.click(screen.getByRole('button', { name: '开始导入' }))
    expect(await screen.findByText(/已处理 1\/3/)).toBeInTheDocument()
  })

  it('P1-D:点浏览打开目录弹窗,下钻后选择回填导入路径', async () => {
    const list = vi
      .spyOn(api.filesystem, 'list')
      .mockResolvedValueOnce({ path: '', parent: null, directories: ['C:\\', 'D:\\'] })
      .mockResolvedValueOnce({ path: 'D:\\', parent: 'D:\\', directories: ['番剧'] })
      .mockResolvedValueOnce({ path: 'D:\\番剧', parent: 'D:\\', directories: ['Show'] })
    renderPage(<PipelinePage />, { factory: controlledFactory() })
    await screen.findByTestId('pipeline-node-归档')
    await userEvent.setup().click(screen.getByRole('button', { name: '选择目录' }))
    // 打开时从盘符根视图加载
    expect(await screen.findByTestId('picker-row-D:\\')).toBeInTheDocument()
    // 下钻:此电脑 → D:\ → 番剧
    fireEvent.click(screen.getByTestId('picker-row-D:\\'))
    expect(await screen.findByTestId('picker-row-番剧')).toBeInTheDocument()
    expect(list).toHaveBeenLastCalledWith('D:\\')
    fireEvent.click(screen.getByTestId('picker-row-番剧'))
    await waitFor(() => expect(list).toHaveBeenLastCalledWith('D:\\番剧'))
    // 选择当前目录 → 回填导入路径输入框并关闭弹窗
    fireEvent.click(screen.getByRole('button', { name: '选择当前目录' }))
    await waitFor(() => {
      expect(screen.queryByTestId('folder-picker-dialog')).not.toBeInTheDocument()
    })
    expect(screen.getByLabelText('导入目录')).toHaveValue('D:\\番剧')
  })
})
