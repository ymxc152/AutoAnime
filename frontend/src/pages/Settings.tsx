/*
 * Settings —— 配置中心(12-E):七标签页 Tabs(运行|识别|下载器|洗版|调度|通知|环境),
 * 对齐 12-D 后端 SettingsOut/SettingsUpdateIn(39 项白名单,extra=forbid)三档生效语义:
 * immediate / scheduler_rebuild / requires_restart(PUT 响应 applied 逐字段提示)。
 * 密钥纪律:GET 只回 has_*;PUT 空串 = 不修改、显式 null = 清除(RSS token 惯例);
 * notify_timeout_s 不在后端白名单,不渲染(以代码为准)。
 * 单一保存按钮(页头)+ 全标签页共享一份 edit 草稿;分组 dirty 在 Tab 上显小圆点;
 * dirty 时路由离开需确认(useBlocker 拦截侧栏点击 + 浏览器返回)。
 */
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useBlocker } from 'react-router-dom'
import { toast } from 'sonner'
import { api, ApiError, getApiToken, setApiToken } from '../api'
import { useApi } from '../hooks/useApi'
import { strings, t } from '../strings'
import { confirmDialog } from '../lib/confirm'
import {
  Badge,
  Button,
  Card,
  Checkbox,
  ErrorState,
  Input,
  PageTitle,
  Select,
  SettingRow,
  Skeleton,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../components'
import type { SettingsDto, SettingsUpdateBody } from '../api/types'

/** 逗号分隔串 → 参考源数组(split/trim/去空) */
function parseOrder(raw: string): string[] {
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

// ---------------------------------------------------------------------------
// 12-E:草稿与字段分组(标签页 dirty 追踪)
// ---------------------------------------------------------------------------

/**
 * 编辑草稿:布尔/文本直接存目标值;数字字段以字符串保真输入
 * (小数点/中途态不被 Number 抹掉),保存时统一转换;空串 = 不修改。
 */
interface SettingsDraft {
  // 布尔
  dry_run?: boolean
  l2_enabled?: boolean
  llm_enabled?: boolean
  reference_enabled?: boolean
  scheduler_enabled?: boolean
  notify_enabled?: boolean
  // 文本 / Select
  log_level?: string
  llm_model?: string
  llm_base_url?: string
  downloader?: string
  qbittorrent_host?: string
  qbittorrent_username?: string
  notify_telegram_chat_id?: string
  upgrade_copy_policy?: string
  naming_title_language?: string
  // 数字(字符串草稿)
  llm_timeout_s?: string
  llm_max_retries?: string
  reference_qps?: string
  pending_backlog_alert_threshold?: string
  rss_poll_interval_minutes?: string
  rss_poll_jitter_pct?: string
  download_poll_interval_s?: string
  download_max_retries?: string
  collected_check_days?: string
  qbittorrent_port?: string
  upgrade_threshold?: string
  upgrade_max_per_episode?: string
  upgrade_skip_size_gb?: string
  mismatch_backfill_budget?: string
  // 参考源顺序(commitOrder 写回)
  reference_order?: string[]
  // 密钥(输入草稿;留空提交时不进 body)
  llm_api_key?: string
  qbittorrent_password?: string
  notify_webhook_url?: string
  notify_telegram_bot_token?: string
}

type DraftKey = keyof SettingsDraft

type TabKey = 'runtime' | 'identify' | 'downloader' | 'upgrade' | 'scheduler' | 'notify' | 'env'

const TAB_KEYS: readonly TabKey[] = [
  'runtime',
  'identify',
  'downloader',
  'upgrade',
  'scheduler',
  'notify',
  'env',
]

/** 各标签页字段归组(与后端 SettingsUpdateIn 白名单一致;reference_order 单独走草稿判定) */
const TAB_FIELD_KEYS: Record<TabKey, readonly string[]> = {
  runtime: ['dry_run', 'l2_enabled', 'llm_enabled', 'log_level'],
  identify: [
    'llm_model',
    'llm_base_url',
    'llm_api_key',
    'llm_timeout_s',
    'llm_max_retries',
    'reference_enabled',
    'reference_order',
    'reference_qps',
  ],
  downloader: [
    'downloader',
    'qbittorrent_host',
    'qbittorrent_port',
    'qbittorrent_username',
    'qbittorrent_password',
  ],
  upgrade: [
    'upgrade_threshold',
    'upgrade_max_per_episode',
    'upgrade_copy_policy',
    'upgrade_skip_size_gb',
    'mismatch_backfill_budget',
    'naming_title_language',
  ],
  scheduler: [
    'scheduler_enabled',
    'rss_poll_interval_minutes',
    'rss_poll_jitter_pct',
    'download_poll_interval_s',
    'download_max_retries',
    'collected_check_days',
    'pending_backlog_alert_threshold',
  ],
  notify: ['notify_enabled', 'notify_webhook_url', 'notify_telegram_bot_token', 'notify_telegram_chat_id'],
  env: [],
}

/** 允许小数点的数字字段(其余按整数过滤) */
const FLOAT_KEYS: ReadonlySet<string> = new Set([
  'llm_timeout_s',
  'reference_qps',
  'upgrade_threshold',
  'upgrade_skip_size_gb',
])

// ---- 草稿字段按类型分组(dirty 逐值比对与 buildPayload 共用;顺序无关) ----
const BOOL_KEYS = [
  'dry_run',
  'l2_enabled',
  'llm_enabled',
  'reference_enabled',
  'scheduler_enabled',
  'notify_enabled',
] as const
const TEXT_KEYS = [
  'log_level',
  'llm_model',
  'llm_base_url',
  'downloader',
  'qbittorrent_host',
  'qbittorrent_username',
  'notify_telegram_chat_id',
  'upgrade_copy_policy',
  'naming_title_language',
] as const
const NUM_KEYS = [
  'llm_timeout_s',
  'llm_max_retries',
  'reference_qps',
  'pending_backlog_alert_threshold',
  'rss_poll_interval_minutes',
  'rss_poll_jitter_pct',
  'download_poll_interval_s',
  'download_max_retries',
  'collected_check_days',
  'qbittorrent_port',
  'upgrade_threshold',
  'upgrade_max_per_episode',
  'upgrade_skip_size_gb',
  'mismatch_backfill_budget',
] as const
const SECRET_KEYS = ['llm_api_key', 'qbittorrent_password', 'notify_webhook_url', 'notify_telegram_bot_token'] as const

/** applied 字段 → 展示标签(逐字段生效 toast 用) */
const FIELD_LABELS: Record<string, string> = {
  dry_run: strings.settings.dryRun,
  l2_enabled: strings.settings.l2Enabled,
  llm_enabled: strings.settings.llmEnabled,
  llm_model: strings.settings.llmModel,
  reference_enabled: strings.settings.referenceEnabled,
  reference_order: strings.settings.referenceOrder,
  llm_timeout_s: strings.settings.llmTimeout,
  llm_max_retries: strings.settings.llmMaxRetries,
  reference_qps: strings.settings.referenceQps,
  pending_backlog_alert_threshold: strings.settings.pendingBacklogThreshold,
  log_level: strings.settings.logLevel,
  scheduler_enabled: strings.settings.schedulerEnabled,
  rss_poll_interval_minutes: strings.settings.rssPollInterval,
  rss_poll_jitter_pct: strings.settings.rssPollJitter,
  download_poll_interval_s: strings.settings.downloadPollInterval,
  download_max_retries: strings.settings.downloadMaxRetries,
  collected_check_days: strings.settings.collectedCheckDays,
  llm_base_url: strings.settings.llmBaseUrl,
  llm_api_key: strings.settings.llmApiKey,
  tmdb_api_key: strings.settings.tmdbApiKey,
  downloader: strings.settings.downloaderKind,
  qbittorrent_host: strings.settings.qbHost,
  qbittorrent_port: strings.settings.qbPort,
  qbittorrent_username: strings.settings.qbUser,
  qbittorrent_password: strings.settings.qbPassword,
  notify_enabled: strings.settings.notifyEnabled,
  notify_webhook_url: strings.settings.notifyWebhookUrl,
  notify_telegram_bot_token: strings.settings.notifyTelegramToken,
  notify_telegram_chat_id: strings.settings.notifyTelegramChatId,
  upgrade_threshold: strings.settings.upgradeThreshold,
  upgrade_max_per_episode: strings.settings.upgradeMaxPerEpisode,
  upgrade_copy_policy: strings.settings.upgradeCopyPolicy,
  upgrade_skip_size_gb: strings.settings.upgradeSkipSizeGb,
  mismatch_backfill_budget: strings.settings.mismatchBackfillBudget,
  naming_title_language: strings.settings.namingTitleLanguage,
  rss_fetch_timeout_s: strings.settings.rssFetchTimeout,
  rss_fetch_retries: strings.settings.rssFetchRetries,
}

// ---------------------------------------------------------------------------
// 密钥行(密码输入 + has_* 占位 + 清除勾选)
// ---------------------------------------------------------------------------

interface SecretRowProps {
  label: string
  hint?: string
  id: string
  /** 输入草稿值(密钥永不回显,恒为用户输入) */
  value: string
  has: boolean
  onChange: (value: string) => void
  clearChecked: boolean
  onClearChange: (checked: boolean) => void
}

function SecretRow({ label, hint, id, value, has, onChange, clearChecked, onClearChange }: SecretRowProps) {
  return (
    <SettingRow label={label} description={hint} htmlFor={id}>
      <div className="flex flex-col gap-1.5">
        <Input
          id={id}
          type="password"
          value={value}
          placeholder={
            has ? strings.settings.secretPlaceholderConfigured : strings.settings.secretPlaceholderNotConfigured
          }
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
          className="data-text"
        />
        {has && (
          // 勾选框与 label 平级(htmlFor 关联):包进 <label> 会因 label 激活行为双触发 onCheckedChange
          <div className="flex items-center gap-1.5">
            <Checkbox
              id={`${id}-clear`}
              checked={clearChecked}
              onCheckedChange={(checked) => onClearChange(checked === true)}
              aria-label={`${strings.settings.clearSecret}:${label}`}
            />
            <label htmlFor={`${id}-clear`} className="text-xs text-ink-secondary">
              {strings.settings.clearSecret}
            </label>
          </div>
        )}
      </div>
    </SettingRow>
  )
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export function SettingsPage() {
  const fetcher = useCallback(() => api.settings.get(), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [edit, setEdit] = useState<SettingsDraft>({})
  // 密钥「清除」勾选(勾选 = 提交 null,独立于输入草稿)
  const [clearSecrets, setClearSecrets] = useState<Partial<Record<DraftKey, boolean>>>({})
  // 保存成功后立即以服务端返回值为展示基线(useApi 的 data 要等 reload 才更新)
  const [savedSnapshot, setSavedSnapshot] = useState<SettingsDto | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  // API Token 本端注入(A3):setApiToken 此前零调用,后端开 token 认证时 WebUI 无入口配置。
  // 与 sse.ts buildEventsUrl 同一 localStorage key(autoanime-api-token)
  const [tokenDraft, setTokenDraft] = useState<string>(() => getApiToken())
  const [tokenNotice, setTokenNotice] = useState<string | null>(null)
  // reference_order 编辑草稿:onChange 只更新草稿保真原文(末尾逗号不被回显抹掉),
  // blur/保存时才 split/trim/filter 写回 edit(回归 A1:打字无法追加第二个参考源)。
  // 本页服务端基线只在 save() 后变化(无其他 reload 路径),草稿在 save 成功时显式重置。
  const [orderDraft, setOrderDraft] = useState<string | null>(null)
  // 测试按钮 loading(测试动作不改 dirty:不触碰 edit/clearSecrets)
  const [qbitTesting, setQbitTesting] = useState(false)
  const [notifyTesting, setNotifyTesting] = useState(false)

  const patch = (partial: SettingsDraft): void => {
    setEdit((prev) => ({ ...prev, ...partial }))
  }

  // ---- dirty 追踪(载入前 data 为 null,视为无脏态;真正使用时数据已就绪) ----
  const baseOrder = (savedSnapshot ?? data)?.reference_order ?? []
  const canonicalOrder = (edit.reference_order ?? baseOrder).join(',')
  // orderDraft 只覆盖「已敲字未 blur」的脏态;blur 后 edit.reference_order 逐值比对接管
  const orderDirty = orderDraft !== null && orderDraft !== canonicalOrder
  // dirty 与基线(savedSnapshot ?? data)逐值比对:文本改回原值/开关拨回原位/
  // 密钥输入后删空/勾选清除后取消,都不再误报;密钥「清除」勾选本身即清除意图,
  // 勾选态恒为脏,取消勾选自动清脏。
  const baseline = savedSnapshot ?? data
  const dirtyKeySet = new Set<string>()
  if (baseline !== null) {
    for (const key of BOOL_KEYS) {
      if (edit[key] !== undefined && edit[key] !== baseline[key]) dirtyKeySet.add(key)
    }
    for (const key of TEXT_KEYS) {
      if (edit[key] !== undefined && edit[key] !== (baseline[key] ?? '')) dirtyKeySet.add(key)
    }
    for (const key of NUM_KEYS) {
      const draft = edit[key]
      if (draft === undefined) continue
      const value = baseline[key]
      const baseStr = value === null || value === undefined ? '' : String(value)
      if (draft !== baseStr) dirtyKeySet.add(key)
    }
    for (const key of SECRET_KEYS) {
      // 密钥输入草稿:空串 = 无输入意图,不算脏;非空 = 将提交明文
      if (edit[key] !== undefined && edit[key] !== '') dirtyKeySet.add(key)
      // 清除勾选:勾选本身即清除意图
      if (clearSecrets[key] === true) dirtyKeySet.add(key)
    }
    if (edit.reference_order !== undefined) {
      if (edit.reference_order.join(',') !== baseOrder.join(',')) dirtyKeySet.add('reference_order')
    }
  }
  const tabDirty = (tab: TabKey): boolean =>
    tab === 'identify'
      ? TAB_FIELD_KEYS.identify.some((key) => dirtyKeySet.has(key)) || orderDirty
      : TAB_FIELD_KEYS[tab].some((key) => dirtyKeySet.has(key))
  const dirty = TAB_KEYS.some(tabDirty)

  // 未保存更改时,路由离开需确认(useBlocker 拦截侧栏点击 + 浏览器返回)
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  )
  useEffect(() => {
    if (blocker.state === 'blocked') {
      void confirmDialog(strings.settings.unsavedLeaveConfirm).then((leave) => {
        if (leave) blocker.proceed()
        else blocker.reset()
      })
    }
  }, [blocker])

  if (error !== null) {
    return (
      <>
        <PageTitle title={strings.settings.title} />
        <ErrorState message={error} onRetry={reload} />
      </>
    )
  }

  if (loading || data === null) {
    return (
      <>
        <PageTitle title={strings.settings.title} />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      </>
    )
  }

  const base: SettingsDto = savedSnapshot ?? data

  // ---- 展示值:编辑草稿优先,否则回显当前基线 ----
  const boolValue = (key: 'dry_run' | 'l2_enabled' | 'llm_enabled' | 'reference_enabled' | 'scheduler_enabled' | 'notify_enabled'): boolean =>
    edit[key] ?? base[key]
  const textValue = (key: 'llm_model' | 'llm_base_url' | 'downloader' | 'qbittorrent_host' | 'qbittorrent_username' | 'notify_telegram_chat_id' | 'upgrade_copy_policy' | 'naming_title_language' | 'log_level'): string => {
    const draft = edit[key]
    if (draft !== undefined) return draft
    const value = base[key]
    return value === null || value === undefined ? '' : value
  }
  const numValue = (
    key:
      | 'llm_timeout_s'
      | 'llm_max_retries'
      | 'reference_qps'
      | 'pending_backlog_alert_threshold'
      | 'rss_poll_interval_minutes'
      | 'rss_poll_jitter_pct'
      | 'download_poll_interval_s'
      | 'download_max_retries'
      | 'collected_check_days'
      | 'qbittorrent_port'
      | 'upgrade_threshold'
      | 'upgrade_max_per_episode'
      | 'upgrade_skip_size_gb'
      | 'mismatch_backfill_budget',
  ): string => {
    const draft = edit[key]
    if (draft !== undefined) return draft
    const value = base[key]
    return value === null || value === undefined ? '' : String(value)
  }
  const secretValue = (key: 'llm_api_key' | 'qbittorrent_password' | 'notify_webhook_url' | 'notify_telegram_bot_token'): string =>
    edit[key] ?? ''

  /** 数字过滤:整数只留数字;浮点允许一个小数点(inputMode=numeric + 过滤,对齐 Pending 做法) */
  const setNum = (key: DraftKey, raw: string): void => {
    const cleaned = raw.replace(/[^\d.]/g, '')
    const idx = cleaned.indexOf('.')
    const filtered =
      idx === -1 || FLOAT_KEYS.has(key)
        ? idx === -1
          ? cleaned
          : cleaned.slice(0, idx + 1) + cleaned.slice(idx + 1).replace(/\./g, '')
        : cleaned.replace(/\./g, '')
    setEdit((prev) => ({ ...prev, [key]: filtered }))
  }

  /** 参考源顺序:blur 提交归一化草稿;与基线一致时不制造脏态 */
  const commitOrder = (): void => {
    if (orderDraft === null) return
    const parsed = parseOrder(orderDraft)
    if (parsed.join(',') !== canonicalOrder) {
      patch({ reference_order: parsed })
    }
    setOrderDraft(parsed.join(',') === canonicalOrder ? null : parsed.join(','))
  }

  /** 草稿 → PUT body(白名单 39 项子集;密钥空串不进 body、勾选清除提交 null) */
  const buildPayload = (): SettingsUpdateBody => {
    const body: SettingsUpdateBody = {}
    for (const key of BOOL_KEYS) {
      const value = edit[key]
      if (value !== undefined) body[key] = value
    }
    for (const key of TEXT_KEYS) {
      const value = edit[key]
      if (value !== undefined) body[key] = value
    }
    for (const key of NUM_KEYS) {
      const raw = edit[key]
      if (raw !== undefined && raw !== '') {
        const parsed = Number(raw)
        if (!Number.isNaN(parsed)) body[key] = parsed
      }
    }
    // 密钥:清除勾选优先(提交 null);否则非空输入才提交;留空 = 不修改
    for (const key of SECRET_KEYS) {
      if (clearSecrets[key]) body[key] = null
      else if (edit[key] !== undefined && edit[key] !== '') body[key] = edit[key]
    }
    // 保存即提交:参考源草稿尚未 blur 也归一化写入本次请求(与 blur 提交同语义)
    const orderSource = orderDraft !== null ? parseOrder(orderDraft) : edit.reference_order
    if (orderSource !== undefined) body.reference_order = orderSource
    return body
  }

  const save = async (): Promise<void> => {
    setSaving(true)
    setSaveError(null)
    try {
      const savedSettings = await api.settings.update(buildPayload())
      setSavedSnapshot(savedSettings)
      setEdit({})
      setClearSecrets({})
      setOrderDraft(null)
      setSaved(true)
      toast.success(strings.settings.saved)
      window.setTimeout(() => setSaved(false), 2500)
      // 12-E:applied 逐字段生效档位 toast
      for (const [key, effect] of Object.entries(savedSettings.applied)) {
        const label = FIELD_LABELS[key] ?? key
        if (effect === 'immediate') toast.success(`${label} ${strings.settings.effectImmediate}`)
        else if (effect === 'scheduler_rebuild') toast.success(`${label} ${strings.settings.effectScheduler}`)
        else toast.warning(`${label} ${strings.settings.effectRestart}`)
      }
      if (savedSettings.warnings.length > 0) {
        toast.warning(`${strings.settings.warningsTitle}: ${savedSettings.warnings.join('; ')}`)
      }
    } catch (cause) {
      setSaveError(cause instanceof ApiError ? cause.message : strings.settings.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  /** qBittorrent 连接测试(不改 dirty:只外呼,不写配置) */
  const runQbitTest = async (): Promise<void> => {
    setQbitTesting(true)
    try {
      const result = await api.settings.qbitTest()
      if (result.ok) {
        toast.success(
          result.version
            ? `${strings.settings.qbitTestOk}(${result.version})`
            : strings.settings.qbitTestOk,
        )
      } else {
        toast.error(`${strings.settings.qbitTestFail}: ${result.error ?? strings.common.unknown}`)
      }
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setQbitTesting(false)
    }
  }

  /** 通知通道测试(逐通道结果 toast;notify_enabled 不阻塞手工测试) */
  const runNotifyTest = async (): Promise<void> => {
    setNotifyTesting(true)
    try {
      const out = await api.settings.notifyTest()
      if (out.results.length === 0) {
        toast.warning(strings.settings.notifyTestNoChannels)
        return
      }
      const failed = out.results.filter((item) => !item.ok)
      if (failed.length === 0) {
        toast.success(
          `${strings.settings.notifyTestOk}(${out.results.map((item) => item.channel).join(', ')})`,
        )
      } else {
        toast.error(
          `${strings.settings.notifyTestFail} ${failed
            .map((item) => `${item.channel}: ${item.error ?? strings.common.unknown}`)
            .join('; ')}`,
        )
      }
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setNotifyTesting(false)
    }
  }

  /** 保存/清除 API Token(写 localStorage);token 由 api client 每请求动态读取,即时生效无需刷新 */
  const saveToken = (): void => {
    const token = tokenDraft.trim()
    setApiToken(token)
    setTokenDraft(token)
    setTokenNotice(
      token === '' ? strings.settings.apiTokenClearedNotice : strings.settings.apiTokenSavedNotice,
    )
  }

  const clearToken = (): void => {
    setApiToken('')
    setTokenDraft('')
    setTokenNotice(strings.settings.apiTokenClearedNotice)
  }

  const switchRow = (
    key: 'dry_run' | 'l2_enabled' | 'llm_enabled' | 'reference_enabled' | 'scheduler_enabled' | 'notify_enabled',
    label: string,
    hint: string,
  ): ReactNode => (
    <SettingRow label={label} description={hint}>
      <Switch
        checked={boolValue(key)}
        onChange={(checked) => patch({ [key]: checked })}
        aria-label={label}
      />
    </SettingRow>
  )

  return (
    <>
      <PageTitle
        title={strings.settings.title}
        description={strings.settings.runtimeHint}
        actions={
          <>
            {dirty && <span className="text-xs text-ink-secondary">未保存更改</span>}
            {!dirty && saved && <span className="text-xs text-success">{strings.settings.saved}</span>}
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={() => void save()}>
              {strings.common.save}
            </Button>
          </>
        }
      />

      {saveError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.settings.saveFailed}</strong>
          {saveError}
        </div>
      )}

      <Tabs defaultValue="runtime">
        <TabsList>
          {TAB_KEYS.map((tab) => (
            <TabsTrigger key={tab} value={tab}>
              {strings.settings.tabs[tab]}
              {tabDirty(tab) && (
                <span
                  role="img"
                  aria-label={t(strings.settings.dirtyDot, { tab: strings.settings.tabs[tab] })}
                  className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-primary"
                />
              )}
            </TabsTrigger>
          ))}
        </TabsList>

        {/* ---- 运行 ---- */}
        <TabsContent value="runtime">
          <Card title={strings.settings.tabs.runtime}>
            <div className="divide-y divide-line">
              {switchRow('dry_run', strings.settings.dryRun, strings.settings.dryRunHint)}
              {switchRow('l2_enabled', strings.settings.l2Enabled, strings.settings.l2EnabledHint)}
              {switchRow('llm_enabled', strings.settings.llmEnabled, strings.settings.llmEnabledHint)}
              <SettingRow label={strings.settings.logLevel} description={strings.settings.logLevelHint}>
                <Select
                  aria-label={strings.settings.logLevel}
                  value={textValue('log_level')}
                  onChange={(e) => patch({ log_level: e.target.value })}
                >
                  {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </Select>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 识别 ---- */}
        <TabsContent value="identify">
          <Card title={strings.settings.tabs.identify}>
            <div className="divide-y divide-line">
              <SettingRow label={strings.settings.llmModel} htmlFor="settings-llm-model">
                <Input
                  id="settings-llm-model"
                  value={textValue('llm_model')}
                  onChange={(e) => patch({ llm_model: e.target.value })}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.llmBaseUrl}
                description={strings.settings.llmBaseUrlHint}
                htmlFor="settings-llm-base-url"
              >
                <Input
                  id="settings-llm-base-url"
                  value={textValue('llm_base_url')}
                  onChange={(e) => patch({ llm_base_url: e.target.value })}
                  className="data-text"
                />
              </SettingRow>
              <SecretRow
                label={strings.settings.llmApiKey}
                id="settings-llm-api-key"
                value={secretValue('llm_api_key')}
                has={base.has_llm_api_key}
                onChange={(value) => patch({ llm_api_key: value })}
                clearChecked={clearSecrets.llm_api_key === true}
                onClearChange={(checked) => setClearSecrets((prev) => ({ ...prev, llm_api_key: checked }))}
              />
              <SettingRow label={strings.settings.llmTimeout} htmlFor="settings-llm-timeout">
                <Input
                  id="settings-llm-timeout"
                  inputMode="decimal"
                  value={numValue('llm_timeout_s')}
                  onChange={(e) => setNum('llm_timeout_s', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.llmMaxRetries} htmlFor="settings-llm-retries">
                <Input
                  id="settings-llm-retries"
                  inputMode="numeric"
                  value={numValue('llm_max_retries')}
                  onChange={(e) => setNum('llm_max_retries', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              {switchRow(
                'reference_enabled',
                strings.settings.referenceEnabled,
                strings.settings.referenceEnabledHint,
              )}
              <SettingRow
                label={strings.settings.referenceOrder}
                description={strings.settings.referenceOrderHint}
                htmlFor="settings-reference-order"
              >
                <Input
                  id="settings-reference-order"
                  value={orderDraft ?? canonicalOrder}
                  onChange={(e) => setOrderDraft(e.target.value)}
                  onBlur={commitOrder}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.referenceQps}
                description={strings.settings.referenceQpsHint}
                htmlFor="settings-reference-qps"
              >
                <Input
                  id="settings-reference-qps"
                  inputMode="decimal"
                  value={numValue('reference_qps')}
                  onChange={(e) => setNum('reference_qps', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.tmdbApiKey} description={strings.settings.secretHint}>
                <Badge tone={base.has_tmdb_api_key ? 'success' : 'neutral'} mark>
                  {base.has_tmdb_api_key ? strings.settings.configured : strings.settings.notConfigured}
                </Badge>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 下载器 ---- */}
        <TabsContent value="downloader">
          <Card title={strings.settings.tabs.downloader}>
            <div className="divide-y divide-line">
              <SettingRow label={strings.settings.downloaderKind} htmlFor="settings-downloader">
                <Select
                  id="settings-downloader"
                  value={textValue('downloader')}
                  onChange={(e) => patch({ downloader: e.target.value })}
                >
                  <option value="qbittorrent">qBittorrent</option>
                  <option value="aria2">aria2</option>
                </Select>
              </SettingRow>
              <SettingRow label={strings.settings.qbHost} htmlFor="settings-qb-host">
                <Input
                  id="settings-qb-host"
                  value={textValue('qbittorrent_host')}
                  onChange={(e) => patch({ qbittorrent_host: e.target.value })}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.qbPort} htmlFor="settings-qb-port">
                <Input
                  id="settings-qb-port"
                  inputMode="numeric"
                  value={numValue('qbittorrent_port')}
                  onChange={(e) => setNum('qbittorrent_port', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.qbUser} htmlFor="settings-qb-user">
                <Input
                  id="settings-qb-user"
                  value={textValue('qbittorrent_username')}
                  onChange={(e) => patch({ qbittorrent_username: e.target.value })}
                  className="data-text"
                />
              </SettingRow>
              <SecretRow
                label={strings.settings.qbPassword}
                id="settings-qb-password"
                value={secretValue('qbittorrent_password')}
                has={base.has_qbittorrent_password}
                onChange={(value) => patch({ qbittorrent_password: value })}
                clearChecked={clearSecrets.qbittorrent_password === true}
                onClearChange={(checked) =>
                  setClearSecrets((prev) => ({ ...prev, qbittorrent_password: checked }))
                }
              />
              <SettingRow
                label={strings.settings.testQbit}
                description={strings.settings.secretHint}
              >
                <Button loading={qbitTesting} onClick={() => void runQbitTest()}>
                  {qbitTesting ? strings.settings.qbitTesting : strings.settings.testQbit}
                </Button>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 洗版 ---- */}
        <TabsContent value="upgrade">
          <Card title={strings.settings.tabs.upgrade}>
            <div className="divide-y divide-line">
              <SettingRow label={strings.settings.upgradeThreshold} htmlFor="settings-upgrade-threshold">
                <Input
                  id="settings-upgrade-threshold"
                  inputMode="decimal"
                  value={numValue('upgrade_threshold')}
                  onChange={(e) => setNum('upgrade_threshold', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.upgradeMaxPerEpisode}
                htmlFor="settings-upgrade-max"
              >
                <Input
                  id="settings-upgrade-max"
                  inputMode="numeric"
                  value={numValue('upgrade_max_per_episode')}
                  onChange={(e) => setNum('upgrade_max_per_episode', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.upgradeCopyPolicy} htmlFor="settings-copy-policy">
                <Select
                  id="settings-copy-policy"
                  value={textValue('upgrade_copy_policy')}
                  onChange={(e) => patch({ upgrade_copy_policy: e.target.value })}
                >
                  <option value="allow">{strings.settings.copyPolicyAllow}</option>
                  <option value="strict">{strings.settings.copyPolicyStrict}</option>
                </Select>
              </SettingRow>
              <SettingRow label={strings.settings.upgradeSkipSizeGb} htmlFor="settings-upgrade-skip">
                <Input
                  id="settings-upgrade-skip"
                  inputMode="decimal"
                  value={numValue('upgrade_skip_size_gb')}
                  onChange={(e) => setNum('upgrade_skip_size_gb', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.mismatchBackfillBudget}
                htmlFor="settings-backfill-budget"
              >
                <Input
                  id="settings-backfill-budget"
                  inputMode="numeric"
                  value={numValue('mismatch_backfill_budget')}
                  onChange={(e) => setNum('mismatch_backfill_budget', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.namingTitleLanguage}
                description={strings.settings.namingTitleLanguageHint}
                htmlFor="settings-naming-lang"
              >
                <Input
                  id="settings-naming-lang"
                  value={textValue('naming_title_language')}
                  onChange={(e) => patch({ naming_title_language: e.target.value })}
                  className="data-text"
                />
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 调度(scheduler_rebuild 档:保存后自动生效) ---- */}
        <TabsContent value="scheduler">
          <Card title={strings.settings.tabs.scheduler} description={strings.settings.schedulerHint}>
            <div className="divide-y divide-line">
              {switchRow(
                'scheduler_enabled',
                strings.settings.schedulerEnabled,
                strings.settings.schedulerHint,
              )}
              <SettingRow
                label={strings.settings.rssPollInterval}
                htmlFor="settings-rss-interval"
              >
                <Input
                  id="settings-rss-interval"
                  inputMode="numeric"
                  value={numValue('rss_poll_interval_minutes')}
                  onChange={(e) => setNum('rss_poll_interval_minutes', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.rssPollJitter} htmlFor="settings-rss-jitter">
                <Input
                  id="settings-rss-jitter"
                  inputMode="numeric"
                  value={numValue('rss_poll_jitter_pct')}
                  onChange={(e) => setNum('rss_poll_jitter_pct', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.downloadPollInterval}
                htmlFor="settings-download-interval"
              >
                <Input
                  id="settings-download-interval"
                  inputMode="numeric"
                  value={numValue('download_poll_interval_s')}
                  onChange={(e) => setNum('download_poll_interval_s', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.downloadMaxRetries}
                htmlFor="settings-download-retries"
              >
                <Input
                  id="settings-download-retries"
                  inputMode="numeric"
                  value={numValue('download_max_retries')}
                  onChange={(e) => setNum('download_max_retries', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.collectedCheckDays}
                htmlFor="settings-collected-days"
              >
                <Input
                  id="settings-collected-days"
                  inputMode="numeric"
                  value={numValue('collected_check_days')}
                  onChange={(e) => setNum('collected_check_days', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow
                label={strings.settings.pendingBacklogThreshold}
                htmlFor="settings-backlog-threshold"
              >
                <Input
                  id="settings-backlog-threshold"
                  inputMode="numeric"
                  value={numValue('pending_backlog_alert_threshold')}
                  onChange={(e) => setNum('pending_backlog_alert_threshold', e.target.value)}
                  className="data-text"
                />
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 通知 ---- */}
        <TabsContent value="notify">
          <Card title={strings.settings.tabs.notify}>
            <div className="divide-y divide-line">
              {switchRow('notify_enabled', strings.settings.notifyEnabled, strings.settings.secretHint)}
              <SecretRow
                label={strings.settings.notifyWebhookUrl}
                id="settings-notify-webhook"
                value={secretValue('notify_webhook_url')}
                has={base.has_notify_webhook_url}
                onChange={(value) => patch({ notify_webhook_url: value })}
                clearChecked={clearSecrets.notify_webhook_url === true}
                onClearChange={(checked) =>
                  setClearSecrets((prev) => ({ ...prev, notify_webhook_url: checked }))
                }
              />
              <SecretRow
                label={strings.settings.notifyTelegramToken}
                id="settings-notify-token"
                value={secretValue('notify_telegram_bot_token')}
                has={base.has_notify_telegram_bot_token}
                onChange={(value) => patch({ notify_telegram_bot_token: value })}
                clearChecked={clearSecrets.notify_telegram_bot_token === true}
                onClearChange={(checked) =>
                  setClearSecrets((prev) => ({ ...prev, notify_telegram_bot_token: checked }))
                }
              />
              <SettingRow
                label={strings.settings.notifyTelegramChatId}
                htmlFor="settings-notify-chat-id"
              >
                <Input
                  id="settings-notify-chat-id"
                  value={textValue('notify_telegram_chat_id')}
                  onChange={(e) => patch({ notify_telegram_chat_id: e.target.value })}
                  className="data-text"
                />
              </SettingRow>
              <SettingRow label={strings.settings.testNotify}>
                <Button loading={notifyTesting} onClick={() => void runNotifyTest()}>
                  {notifyTesting ? strings.settings.notifyTesting : strings.settings.testNotify}
                </Button>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 环境(只读 + API Token 本端注入,保持现状) ---- */}
        <TabsContent value="env">
          <Card title={strings.settings.environmentSection}>
            <div className="divide-y divide-line">
              <SettingRow label={strings.settings.libraryPath}>
                <span className="data-text break-all text-sm text-ink">{base.library_path}</span>
              </SettingRow>
              <SettingRow label={strings.settings.downloadPath}>
                <span className="data-text break-all text-sm text-ink">{base.download_path}</span>
              </SettingRow>
              <SettingRow label={strings.settings.apiEndpoint}>
                <span className="data-text text-sm text-ink">
                  {base.api_host}:{base.api_port}
                </span>
              </SettingRow>
              <SettingRow label={strings.settings.sseHeartbeat}>
                <span className="data-text text-sm text-ink">{base.api_sse_heartbeat_s}s</span>
              </SettingRow>
              <SettingRow label={strings.settings.sseReplay}>
                <span className="data-text text-sm text-ink">{base.api_sse_replay_limit}</span>
              </SettingRow>
              <SettingRow label={strings.settings.apiToken} description={strings.settings.secretHint}>
                <Badge tone={base.has_api_token ? 'success' : 'neutral'} mark>
                  {base.has_api_token ? strings.settings.configured : strings.settings.notConfigured}
                </Badge>
              </SettingRow>
              <SettingRow
                label={strings.settings.apiTokenInput}
                description={strings.settings.apiTokenHint}
                htmlFor="settings-api-token"
              >
                <div className="flex flex-col gap-1.5">
                  <Input
                    id="settings-api-token"
                    type="password"
                    value={tokenDraft}
                    onChange={(e) => {
                      setTokenDraft(e.target.value)
                      setTokenNotice(null)
                    }}
                    autoComplete="off"
                    className="data-text"
                  />
                  <div className="flex gap-2">
                    <Button size="sm" variant="secondary" onClick={saveToken}>
                      {strings.settings.apiTokenSave}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={clearToken}>
                      {strings.settings.apiTokenClear}
                    </Button>
                  </div>
                  {tokenNotice !== null && (
                    <p role="status" className="text-xs text-success">
                      {tokenNotice}
                    </p>
                  )}
                </div>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </>
  )
}
