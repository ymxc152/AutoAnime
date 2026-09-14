/*
 * 首次运行设置向导(#/setup):全屏独立页面,不套 Layout 壳(路由表里与
 * Layout 兄弟级注册)。四步:欢迎 → 下载器连接 → 路径对齐 → 完成;
 * 步骤可回退(点击步骤条/上一步按钮),步骤态存 sessionStorage
 * (key autoanime-setup-step),刷新不丢。
 *
 * 连接测试契约:qbit-test 按「运行时 + DB 覆盖」的合并配置试跑,因此
 * 先把表单连接参数 PUT /api/settings 落库(密码空串 = 不修改已有值,
 * 对齐 settings PUT 密钥语义),再调 qbit-test;成功展示版本与 qB 默认
 * 保存路径,失败停留本步并显示原因。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, Clapperboard } from 'lucide-react'
import { api, ApiError } from '../api'
import type { QbitTestOut, SetupStatusDto } from '../api/types'
import { strings } from '../strings'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

const STEP_STORAGE_KEY = 'autoanime-setup-step'

const STEP_WELCOME = 1
const STEP_DOWNLOADER = 2
const STEP_PATHS = 3
const STEP_DONE = 4

const STEP_LABELS: Record<number, string> = {
  [STEP_WELCOME]: strings.setup.stepWelcome,
  [STEP_DOWNLOADER]: strings.setup.stepDownloader,
  [STEP_PATHS]: strings.setup.stepPaths,
  [STEP_DONE]: strings.setup.stepDone,
}

function readInitialStep(): number {
  try {
    const raw = sessionStorage.getItem(STEP_STORAGE_KEY)
    const step = raw === null ? NaN : Number(raw)
    return step >= STEP_WELCOME && step <= STEP_DONE ? step : STEP_WELCOME
  } catch {
    /* 存储不可用时从头开始 */
    return STEP_WELCOME
  }
}

/* ---------- 步骤条(可点击回退到已过的步骤) ---------- */

function StepBar({ current, onGo }: { current: number; onGo: (step: number) => void }) {
  return (
    <ol className="flex flex-wrap items-center gap-2" data-testid="setup-stepbar">
      {[STEP_WELCOME, STEP_DOWNLOADER, STEP_PATHS, STEP_DONE].map((step, index) => {
        const isCurrent = step === current
        const isPast = step < current
        return (
          <li key={step} className="flex items-center gap-2">
            {index > 0 && <span aria-hidden className="h-px w-4 bg-line" />}
            <button
              type="button"
              disabled={isCurrent}
              onClick={() => onGo(step)}
              className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors duration-[var(--ink-transition-fast)] ${
                isCurrent
                  ? 'bg-primary-light font-semibold text-ink'
                  : isPast
                    ? 'text-ink-secondary hover:bg-surface-2 hover:text-ink'
                    : 'cursor-default text-ink-muted'
              }`}
            >
              {isPast ? (
                <Check className="h-3.5 w-3.5 text-success" aria-hidden />
              ) : (
                <span aria-hidden>{step}</span>
              )}
              {STEP_LABELS[step]}
            </button>
          </li>
        )
      })}
    </ol>
  )
}

export function SetupPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState<number>(readInitialStep)
  const [form, setForm] = useState({ host: '', port: '8080', username: 'admin', password: '' })
  const [formError, setFormError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [qbitTest, setQbitTest] = useState<QbitTestOut | null>(null)
  const [status, setStatus] = useState<SetupStatusDto | null>(null)
  const [statusLoading, setStatusLoading] = useState(false)
  const [goToQbHint, setGoToQbHint] = useState(false)
  const [adopting, setAdopting] = useState(false)
  const [adoptError, setAdoptError] = useState<string | null>(null)
  const [completed, setCompleted] = useState(false)
  // StrictMode 下 effect 双跑:complete 请求幂等(重复写同一覆盖项),但只发一次
  const completeStarted = useRef(false)

  /* 步骤态持久化:刷新后停在当前步 */
  useEffect(() => {
    try {
      sessionStorage.setItem(STEP_STORAGE_KEY, String(step))
    } catch {
      /* 存储不可用时跳过持久化 */
    }
  }, [step])

  /* 预填现有连接配置(GET 不回显密钥,密码留空 = 不修改) */
  useEffect(() => {
    let alive = true
    void api.settings
      .get()
      .then((settings) => {
        if (!alive) return
        setForm((prev) => ({
          ...prev,
          host: settings.qbittorrent_host || prev.host,
          port: String(settings.qbittorrent_port ?? prev.port),
          username: settings.qbittorrent_username || prev.username,
        }))
      })
      .catch(() => {
        /* 预填失败不阻塞向导,保留缺省值 */
      })
    return () => {
      alive = false
    }
  }, [])

  /* 路径对齐检测(进入第 3 步与「重试」共用) */
  const refreshStatus = useCallback(() => {
    setStatusLoading(true)
    void api.setup
      .status()
      .then((next) => {
        setStatus(next)
        setStatusLoading(false)
      })
      .catch((cause: unknown) => {
        setStatus(null)
        setStatusLoading(false)
        setFormError(cause instanceof ApiError ? cause.message : strings.common.loadFailed)
      })
  }, [])

  useEffect(() => {
    if (step !== STEP_PATHS) return
    // 异步起跳(set-state-in-effect 纪律,与 useApi 同款):探测本身是外部请求
    void Promise.resolve().then(refreshStatus)
  }, [step, refreshStatus])

  /* 进入完成步:落 wizard_done 标记(幂等;ref 防 StrictMode 双发;alive 防卸载后 setState) */
  useEffect(() => {
    if (step !== STEP_DONE || completeStarted.current) return
    completeStarted.current = true
    let alive = true
    void api.setup
      .complete()
      .then(() => {
        if (alive) setCompleted(true)
      })
      .catch(() => {
        // 标记失败不阻断「去选番」动线,可经设置页重跑向导
        if (alive) setCompleted(true)
      })
    return () => {
      alive = false
    }
  }, [step])

  /* ② 测试连接并继续:先落库连接参数,再按合并配置试跑 qbit-test */
  const testAndContinue = async (): Promise<void> => {
    setTesting(true)
    setFormError(null)
    try {
      const port = Number(form.port)
      if (!form.host.trim() || !Number.isFinite(port)) {
        setFormError(strings.settings.qbitTestFail)
        return
      }
      await api.settings.update({
        qbittorrent_host: form.host.trim(),
        qbittorrent_port: port,
        qbittorrent_username: form.username,
        // 密码空串 = 不修改已有值(settings PUT 密钥语义);只有填了才提交
        ...(form.password ? { qbittorrent_password: form.password } : {}),
      })
      const result = await api.settings.qbitTest()
      setQbitTest(result)
      if (result.ok) setStep(STEP_PATHS)
      // 失败停留:错误原因在 result.error,交由下方错误区展示
    } catch (cause) {
      setFormError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setTesting(false)
    }
  }

  /* ③ 采用 qB 路径为下载目录(绝对路径,满足 PUT 校验) */
  const adoptQbPath = async (): Promise<void> => {
    if (!status?.qb_save_path) return
    setAdopting(true)
    setAdoptError(null)
    try {
      await api.settings.update({ download_path: status.qb_save_path })
      setStep(STEP_DONE)
    } catch (cause) {
      setAdoptError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setAdopting(false)
    }
  }

  const inputClass =
    'w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus-visible:ring-1 focus-visible:ring-primary'

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 py-8">
      <div className="w-full max-w-xl rounded-xl border border-line bg-surface p-6 shadow-soft-lg">
        {/* 品牌头(与侧栏同款标记) */}
        <div className="mb-5 flex items-center gap-2.5">
          <span
            aria-hidden
            className="flex h-8 w-8 items-center justify-center rounded-lg text-white"
            style={{ backgroundImage: 'linear-gradient(135deg, var(--ink-primary), var(--ink-primary-hover))' }}
          >
            <Clapperboard className="h-4 w-4" />
          </span>
          <div>
            <h1 className="text-base font-semibold text-ink">{strings.setup.wizardTitle}</h1>
            <p className="text-xs text-ink-muted">{strings.setup.wizardSubtitle}</p>
          </div>
        </div>

        <StepBar
          current={step}
          onGo={(target) => {
            if (target <= step) setStep(target) // 只允许回退,前进步走各步的继续按钮
          }}
        />

        <div className="mt-5 flex flex-col gap-4">
          {/* ① 欢迎 */}
          {step === STEP_WELCOME && (
            <section data-testid="setup-step-welcome" className="flex flex-col gap-4">
              <p className="text-sm leading-relaxed text-ink-secondary">{strings.setup.welcomeBody}</p>
              <ol className="flex flex-col gap-2 text-sm text-ink">
                {[strings.setup.stepDownloader, strings.setup.stepPaths, strings.setup.stepDone].map(
                  (label, index) => (
                    <li key={label} className="flex items-center gap-2">
                      <span
                        aria-hidden
                        className="flex h-5 w-5 items-center justify-center rounded-full bg-primary-light text-xs font-semibold"
                      >
                        {index + 1}
                      </span>
                      {label}
                    </li>
                  ),
                )}
              </ol>
              <div className="flex items-center justify-between">
                <Button type="button" onClick={() => setStep(STEP_DOWNLOADER)}>
                  {strings.setup.stepDownloader}
                </Button>
                <button
                  type="button"
                  onClick={() => navigate('/dashboard')}
                  className="text-xs text-ink-muted underline-offset-4 hover:text-ink hover:underline"
                >
                  {strings.setup.skipWizard}
                </button>
              </div>
            </section>
          )}

          {/* ② 下载器连接 */}
          {step === STEP_DOWNLOADER && (
            <section data-testid="setup-step-downloader" className="flex flex-col gap-3">
              <p className="text-sm leading-relaxed text-ink-secondary">{strings.setup.downloaderBody}</p>
              <div className="flex flex-col gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="setup-qb-host" className="text-xs text-ink-secondary">
                    {strings.settings.qbHost}
                  </Label>
                  <Input
                    id="setup-qb-host"
                    value={form.host}
                    onChange={(e) => setForm((prev) => ({ ...prev, host: e.target.value }))}
                    placeholder="http://127.0.0.1:8080"
                    className={inputClass}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="setup-qb-port" className="text-xs text-ink-secondary">
                      {strings.settings.qbPort}
                    </Label>
                    <Input
                      id="setup-qb-port"
                      value={form.port}
                      onChange={(e) => setForm((prev) => ({ ...prev, port: e.target.value }))}
                      className={inputClass}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="setup-qb-username" className="text-xs text-ink-secondary">
                      {strings.settings.qbUser}
                    </Label>
                    <Input
                      id="setup-qb-username"
                      value={form.username}
                      onChange={(e) => setForm((prev) => ({ ...prev, username: e.target.value }))}
                      className={inputClass}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="setup-qb-password" className="text-xs text-ink-secondary">
                    {strings.settings.qbPassword}
                  </Label>
                  <Input
                    id="setup-qb-password"
                    type="password"
                    value={form.password}
                    onChange={(e) => setForm((prev) => ({ ...prev, password: e.target.value }))}
                    className={inputClass}
                  />
                </div>
              </div>
              {qbitTest && !qbitTest.ok && (
                <p data-testid="setup-test-error" className="text-sm font-medium text-danger">
                  {strings.settings.qbitTestFail}: {qbitTest.error ?? strings.common.unknown}
                </p>
              )}
              {formError !== null && (
                <p className="text-sm font-medium text-danger">{formError}</p>
              )}
              {qbitTest?.ok && qbitTest.version && (
                <div data-testid="setup-test-ok" className="flex flex-col gap-0.5">
                  <p className="text-sm font-medium text-success">
                    {strings.settings.qbitTestOk}({qbitTest.version})
                  </p>
                  {qbitTest.save_path && (
                    <p className="text-xs text-ink-muted">
                      {strings.settings.qbSavePath}: {qbitTest.save_path}
                    </p>
                  )}
                </div>
              )}
              <div className="flex items-center justify-between">
                <Button type="button" onClick={() => void testAndContinue()} disabled={testing}>
                  {testing ? strings.settings.qbitTesting : strings.setup.testAndContinue}
                </Button>
                <button
                  type="button"
                  onClick={() => setStep(STEP_WELCOME)}
                  className="text-xs text-ink-muted underline-offset-4 hover:text-ink hover:underline"
                >
                  ‹ {strings.setup.stepWelcome}
                </button>
              </div>
            </section>
          )}

          {/* ③ 路径对齐 */}
          {step === STEP_PATHS && (
            <section data-testid="setup-step-paths" className="flex flex-col gap-3">
              {statusLoading && (
                <p className="text-sm text-ink-muted">{strings.common.loading}</p>
              )}
              {!statusLoading && status?.downloader_reachable && status.paths_aligned && (
                <>
                  <p data-testid="setup-paths-ok" className="text-sm font-medium text-success">
                    {strings.setup.pathsOk}
                  </p>
                  <div className="flex items-center justify-between">
                    <Button type="button" onClick={() => setStep(STEP_DONE)}>
                      {strings.setup.stepDone}
                    </Button>
                    <button
                      type="button"
                      onClick={() => setStep(STEP_DOWNLOADER)}
                      className="text-xs text-ink-muted underline-offset-4 hover:text-ink hover:underline"
                    >
                      ‹ {strings.setup.stepDownloader}
                    </button>
                  </div>
                </>
              )}
              {!statusLoading && status?.downloader_reachable && !status.paths_aligned && (
                <>
                  <p className="text-sm leading-relaxed text-ink-secondary">{strings.setup.pathsMismatch}</p>
                  {status.qb_save_path && (
                    <p className="text-xs text-ink-muted">
                      {strings.settings.qbSavePath}: {status.qb_save_path}
                    </p>
                  )}
                  {adoptError !== null && <p className="text-sm font-medium text-danger">{adoptError}</p>}
                  <div className="flex flex-wrap items-center gap-2">
                    {status.qb_save_path && (
                      <Button type="button" onClick={() => void adoptQbPath()} disabled={adopting}>
                        {strings.setup.adoptQbPath}
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setGoToQbHint(true)
                      }}
                    >
                      {strings.setup.goToQb}
                    </Button>
                    <Button type="button" variant="ghost" onClick={refreshStatus}>
                      {strings.common.retry}
                    </Button>
                  </div>
                  {goToQbHint && (
                    <p data-testid="setup-go-to-qb-hint" className="text-xs leading-relaxed text-ink-secondary">
                      {strings.settings.qbSavePathMismatch} {strings.common.retry} →{' '}
                      {strings.setup.pathsOk}
                    </p>
                  )}
                  <div>
                    <button
                      type="button"
                      onClick={() => setStep(STEP_DOWNLOADER)}
                      className="text-xs text-ink-muted underline-offset-4 hover:text-ink hover:underline"
                    >
                      ‹ {strings.setup.stepDownloader}
                    </button>
                  </div>
                </>
              )}
              {!statusLoading && status !== null && !status.downloader_reachable && (
                <>
                  <p data-testid="setup-paths-unreachable" className="text-sm font-medium text-danger">
                    {strings.settings.qbitTestFail}
                  </p>
                  <div className="flex items-center gap-2">
                    <Button type="button" variant="outline" onClick={refreshStatus}>
                      {strings.common.retry}
                    </Button>
                    <Button type="button" variant="ghost" onClick={() => setStep(STEP_DOWNLOADER)}>
                      ‹ {strings.setup.stepDownloader}
                    </Button>
                  </div>
                </>
              )}
              {!statusLoading && status === null && (
                <Button type="button" variant="outline" onClick={refreshStatus}>
                  {strings.common.retry}
                </Button>
              )}
            </section>
          )}

          {/* ④ 完成 */}
          {step === STEP_DONE && (
            <section data-testid="setup-step-done" className="flex flex-col gap-4">
              <p className="text-sm leading-relaxed text-ink-secondary">{strings.setup.doneBody}</p>
              <Button type="button" onClick={() => navigate('/subscriptions')}>
                {strings.setup.goSubscribe}
              </Button>
              {completed && (
                <p data-testid="setup-complete-ok" className="text-xs text-ink-muted">
                  {strings.common.save} ✓
                </p>
              )}
              <div>
                <button
                  type="button"
                  onClick={() => setStep(STEP_PATHS)}
                  className="text-xs text-ink-muted underline-offset-4 hover:text-ink hover:underline"
                >
                  ‹ {strings.setup.stepPaths}
                </button>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
