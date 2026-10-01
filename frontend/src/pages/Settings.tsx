/*
 * Settings —— 配置中心(12-E):七标签页 Tabs(运行|识别|下载器|洗版|调度|通知|环境),
 * 对齐 12-D 后端 SettingsOut/SettingsUpdateIn(39 项白名单,extra=forbid)三档生效语义:
 * immediate / scheduler_rebuild / requires_restart(保存 toast 按档位聚合计数)。
 * 密钥纪律:GET 只回 has_*;PUT 空串 = 不修改、显式 null = 清除(RSS token 惯例);
 * notify_timeout_s 不在后端白名单,不渲染(以代码为准)。
 * 整页 form 包裹(Tabs 外层):任何输入框回车经隐藏 submit 触发保存;
 * 数字字段带 NUM_META 范围/单位元数据,超范围行内提示 + 禁保存(不拦截输入)。
 * 并发写冲突:PUT 携带 GET 的 updated_at 基线,409 settings_changed 时清草稿并 reload。
 * 单一保存按钮(页头)+ 全标签页共享一份 edit 草稿;分组 dirty 在 Tab 上显小圆点;
 * dirty 时路由离开需确认(useBlocker 拦截侧栏点击 + 浏览器返回)。
 * 行控件统一右缘对齐:SettingRow 控件列固定宽,Input/Select 满宽天然贴右缘,
 * 开关/徽标/按钮/只读值等小控件需显式贴同一右缘(七 tab 一致,实测反馈);
 * qbit 测试结果以行内状态文本展示(成功含版本/失败原因),按钮任何状态下保持
 * Button 组件形态——secondary hover 默认融进卡片底色,点击后指针未移开会呈
 * 「无边框裸文本」,此处以 hover:border-line!/hover:bg-surface-2! 稳住形态。
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
import type { SettingsDto, SettingsUpdateBody, SettingEffect } from '../api/types'

/** 逗号分隔串 → 参考源数组(split/trim/去空) */
function parseOrder(raw: string): string[] {
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

/**
 * 环境只读路径值:优先显示后端 resolve 的绝对路径(library_path_abs/download_path_abs,
 * 用户才知道实际位置);旧后端缺省该字段时回退显示配置值;绝对路径与配置值不同时,
 * 以 muted 小字保留原配置值便于对照。控件列内贴右缘(与各 tab 行控件对齐)。
 */


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
  naming_movie_dir?: boolean
  naming_specials_s00?: boolean
  naming_year_suffix?: boolean
  // 文本 / Select
  log_level?: string
  llm_model?: string
  llm_base_url?: string
  downloader?: string
  qbittorrent_host?: string
  qbittorrent_username?: string
  library_path?: string
  download_path?: string
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
    'naming_movie_dir',
    'naming_specials_s00',
    'naming_year_suffix',
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
  env: ['library_path', 'download_path'],
}

/** 允许小数点的数字字段(其余按整数过滤) */
const FLOAT_KEYS: ReadonlySet<string> = new Set([
  'llm_timeout_s',
  'reference_qps',
  'upgrade_threshold',
  'upgrade_skip_size_gb',
])

/** 数字字段元数据:合法范围 + 单位(backend 契约;qbittorrent_port 后端未约定范围,不在此列) */
interface NumFieldMeta {
  min: number
  max: number
  unit: string
}

type NumericMetaKey =
  | 'llm_timeout_s'
  | 'llm_max_retries'
  | 'reference_qps'
  | 'pending_backlog_alert_threshold'
  | 'rss_poll_interval_minutes'
  | 'rss_poll_jitter_pct'
  | 'download_poll_interval_s'
  | 'download_max_retries'
  | 'collected_check_days'
  | 'upgrade_threshold'
  | 'upgrade_max_per_episode'
  | 'upgrade_skip_size_gb'
  | 'mismatch_backfill_budget'

const NUM_META: Record<NumericMetaKey, NumFieldMeta> = {
  llm_timeout_s: { min: 5, max: 600, unit: strings.uxfix.unitSecond },
  llm_max_retries: { min: 0, max: 10, unit: strings.uxfix.unitTimes },
  reference_qps: { min: 0, max: 100, unit: strings.uxfix.unitTimes },
  pending_backlog_alert_threshold: { min: 1, max: 1000, unit: strings.uxfix.unitTimes },
  rss_poll_interval_minutes: { min: 1, max: 1440, unit: strings.uxfix.unitMinute },
  rss_poll_jitter_pct: { min: 0, max: 50, unit: strings.uxfix.unitPercent },
  download_poll_interval_s: { min: 5, max: 3600, unit: strings.uxfix.unitSecond },
  download_max_retries: { min: 0, max: 10, unit: strings.uxfix.unitTimes },
  collected_check_days: { min: 1, max: 365, unit: '天' },
  upgrade_threshold: { min: 0, max: 100, unit: '分' },
  upgrade_max_per_episode: { min: 1, max: 10, unit: strings.uxfix.unitTimes },
  upgrade_skip_size_gb: { min: 0, max: 10000, unit: strings.uxfix.unitGb },
  mismatch_backfill_budget: { min: 0, max: 10, unit: strings.uxfix.unitTimes },
}

/** 单个数字字段的范围校验:空串 = 不修改(合法);超范围/非数 → 行内提示文案 */
function numRangeError(key: DraftKey, edit: SettingsDraft): string | null {
  const meta = NUM_META[key as NumericMetaKey]
  if (meta === undefined) return null
  const raw = edit[key]
  if (typeof raw !== 'string' || raw === '') return null
  const value = Number(raw)
  if (Number.isNaN(value) || value < meta.min || value > meta.max) {
    return t(strings.uxfix.numberRangeHint, { min: meta.min, max: meta.max })
  }
  return null
}

// ---- 草稿字段按类型分组(dirty 逐值比对与 buildPayload 共用;顺序无关) ----
const BOOL_KEYS = [
  'dry_run',
  'l2_enabled',
  'llm_enabled',
  'reference_enabled',
  'scheduler_enabled',
  'notify_enabled',
  'naming_movie_dir',
  'naming_specials_s00',
  'naming_year_suffix',
] as const
const TEXT_KEYS = [
  'log_level',
  'llm_model',
  'llm_base_url',
  'downloader',
  'qbittorrent_host',
  'qbittorrent_username',
  'library_path',
  'download_path',
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
  // qbit 测试结果行内展示(ok=success 色 / fail=danger 色;不再走 toast 吞按钮反馈)
  const [qbitTestResult, setQbitTestResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [qbSavePath, setQbSavePath] = useState('')
  const [checkingUpdate, setCheckingUpdate] = useState(false)
  const [updateResult, setUpdateResult] = useState<{ text: string; url: string | null } | null>(null)

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

  // 数字字段校验:任一 NUM_META 字段超范围 → 禁保存(行内提示,不拦截输入)
  const numInvalid = (Object.keys(NUM_META) as NumericMetaKey[]).some(
    (key) => numRangeError(key, edit) !== null,
  )

  // ---- 展示值:编辑草稿优先,否则回显当前基线 ----
  const boolValue = (key: (typeof BOOL_KEYS)[number]): boolean => edit[key] ?? base[key]
  const textValue = (key: 'llm_model' | 'llm_base_url' | 'downloader' | 'qbittorrent_host' | 'qbittorrent_username' | 'library_path' | 'download_path' | 'notify_telegram_chat_id' | 'upgrade_copy_policy' | 'naming_title_language' | 'log_level'): string => {
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

  // 媒体库命名(批次一)示例预览:随三开关草稿实时拼装(开启哪路就拼哪路示例)
  const namingExamples = (
    [
      boolValue('naming_movie_dir') ? strings.settings.namingExampleMovie : null,
      boolValue('naming_specials_s00') ? strings.settings.namingExampleSpecial : null,
      boolValue('naming_year_suffix') ? strings.settings.namingExampleYear : null,
    ] as (string | null)[]
  ).filter((item): item is string => item !== null)

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
      const payload = buildPayload()
      // 并发写冲突基线:携带 GET 时的 updated_at,后端不一致回 409
      payload.base_updated_at = base.updated_at ?? null
      const savedSettings = await api.settings.update(payload)
      setSavedSnapshot(savedSettings)
      setEdit({})
      setClearSecrets({})
      setOrderDraft(null)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2500)
      // toast 合并:按生效档位聚合计数(0 的档位省略),替代逐字段弹窗
      const counts: Record<SettingEffect, number> = {
        immediate: 0,
        scheduler_rebuild: 0,
        requires_restart: 0,
      }
      for (const effect of Object.values(savedSettings.applied)) counts[effect] += 1
      const parts: string[] = []
      if (counts.immediate > 0) parts.push(t(strings.uxfix.savedImmediate, { n: counts.immediate }))
      if (counts.scheduler_rebuild > 0)
        parts.push(t(strings.uxfix.savedRebuild, { n: counts.scheduler_rebuild }))
      if (counts.requires_restart > 0)
        parts.push(t(strings.uxfix.savedRestart, { n: counts.requires_restart }))
      toast.success(t(strings.uxfix.savedSummary, { n: Object.keys(savedSettings.applied).length }), {
        description: parts.join(' · '),
      })
      if (savedSettings.warnings.length > 0) {
        toast.warning(t(strings.uxfix.savedWarnings, { n: savedSettings.warnings.length }), {
          description: savedSettings.warnings.join('; '),
        })
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409 && cause.message.includes('settings_changed')) {
        // 并发冲突:以服务端为准——提示 + 清空本地草稿 + reload
        toast.error(strings.uxfix.settingsChangedElsewhere)
        setSavedSnapshot(null)
        setEdit({})
        setClearSecrets({})
        setOrderDraft(null)
        reload()
      } else {
        setSaveError(cause instanceof ApiError ? cause.message : strings.settings.saveFailed)
      }
    } finally {
      setSaving(false)
    }
  }

  /** qBittorrent 连接测试(不改 dirty:只外呼,不写配置;结果走行内状态文本,不走 toast) */
  const runCheckUpdate = async (): Promise<void> => {
    setCheckingUpdate(true)
    setUpdateResult(null)
    try {
      const d = await api.setup.checkUpdate()
      if (d.error !== null) {
        setUpdateResult({ text: `${strings.common.loadFailed}: ${d.error}`, url: null })
      } else if (d.has_update) {
        setUpdateResult({
          text: `${strings.settings.newVersionFound}: ${d.latest}`,
          url: d.changelog_url,
        })
      } else {
        setUpdateResult({ text: strings.settings.upToDate, url: null })
      }
    } catch (cause) {
      setUpdateResult({
        text: cause instanceof ApiError ? cause.message : strings.common.actionFailed,
        url: null,
      })
    } finally {
      setCheckingUpdate(false)
    }
  }

  const runQbitTest = async (): Promise<void> => {
    setQbitTesting(true)
    setQbitTestResult(null)
    try {
      const result = await api.settings.qbitTest()
      if (result.ok) {
        const parts = [
          result.version
            ? `${strings.settings.qbitTestOk}(${result.version})`
            : strings.settings.qbitTestOk,
        ]
        // 下载目录 vs qB 默认保存路径:不一致提示外部 RSS 下载不会被自动扫描看到
        const dlPath = (edit.download_path ?? base.download_path ?? '').trim()
        const qbPath = (result.save_path ?? '').trim()
        if (qbPath !== '' && qbPath !== dlPath) {
          parts.push(strings.settings.qbSavePathMismatch)
        }
        setQbitTestResult({ ok: true, text: parts.join('。') })
        if (qbPath !== '') setQbSavePath(qbPath)
      } else {
        setQbitTestResult({
          ok: false,
          text: `${strings.settings.qbitTestFail}: ${result.error ?? strings.common.unknown}`,
        })
      }
    } catch (cause) {
      setQbitTestResult({
        ok: false,
        text: `${strings.settings.qbitTestFail}: ${
          cause instanceof ApiError ? cause.message : strings.common.actionFailed
        }`,
      })
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

  const switchRow = (key: (typeof BOOL_KEYS)[number], label: string, hint: string): ReactNode => (
    <SettingRow label={label} description={hint}>
      {/* 控件列固定宽:Input/Select 满宽贴右缘,开关小控件显式贴同一右缘(与下载器 tab 行布局一致) */}
      <div className="flex justify-end">
        <Switch
          checked={boolValue(key)}
          onChange={(checked) => patch({ [key]: checked })}
          aria-label={label}
        />
      </div>
    </SettingRow>
  )

  /** 数字字段行:输入 + 「单位 · 允许范围」说明;超范围时行内红字(只提示不拦截输入) */
  const numFieldRow = (key: NumericMetaKey, label: string, baseHint?: string): ReactNode => {
    const meta = NUM_META[key]
    const err = numRangeError(key, edit)
    const metaHint = `单位：${meta.unit} · ${t(strings.uxfix.numberRangeHint, { min: meta.min, max: meta.max })}`
    return (
      <SettingRow
        label={label}
        description={baseHint !== undefined ? `${baseHint}；${metaHint}` : metaHint}
        htmlFor={`settings-${key}`}
      >
        <div className="flex flex-col gap-1">
          <Input
            id={`settings-${key}`}
            inputMode={FLOAT_KEYS.has(key) ? 'decimal' : 'numeric'}
            value={numValue(key)}
            onChange={(e) => setNum(key, e.target.value)}
            className="data-text"
          />
          {err !== null && (
            <p role="alert" className="text-sm font-medium text-danger">
              {err}
            </p>
          )}
        </div>
      </SettingRow>
    )
  }

  return (
    /* 整页 form:任一输入框回车经隐藏 submit 触发保存(保存主按钮在页头 form 外,走 onClick);
       Switch/Button 均为 type="button",不会误触发提交 */
    <form
      onSubmit={(event) => {
        event.preventDefault()
        if (dirty && !numInvalid) void save()
      }}
    >
      <PageTitle
        title={strings.settings.title}
        description={strings.settings.runtimeHint}
        actions={
          <>
            {dirty && <span className="text-xs text-ink-secondary">未保存更改</span>}
            {!dirty && saved && <span className="text-xs text-success">{strings.settings.saved}</span>}
            <Button variant="primary" loading={saving} disabled={!dirty || numInvalid} onClick={() => void save()}>
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
        {/* 窄窗口(375px)七个 tab 装不下一行:允许横向滚动,宽屏不受影响 */}
        <TabsList className="max-w-full overflow-x-auto">
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
              {numFieldRow('llm_timeout_s', strings.settings.llmTimeout)}
              {numFieldRow('llm_max_retries', strings.settings.llmMaxRetries)}
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
              {numFieldRow('reference_qps', strings.settings.referenceQps, strings.settings.referenceQpsHint)}
              <SettingRow label={strings.settings.tmdbApiKey} description={strings.settings.secretHint}>
                <div className="flex justify-end">
                  <Badge tone={base.has_tmdb_api_key ? 'success' : 'neutral'} mark>
                    {base.has_tmdb_api_key ? strings.settings.configured : strings.settings.notConfigured}
                  </Badge>
                </div>
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
                <div className="flex flex-col items-end gap-1.5">
                  {/* hover 稳住 Button 形态:secondary hover 默认融进卡片底色(实测点击后呈裸文本),
                      无论 loading/success/fail 按钮始终带边框与背景 */}
                  <Button
                    loading={qbitTesting}
                    onClick={() => void runQbitTest()}
                    className="hover:border-line! hover:bg-surface-2!"
                  >
                    {qbitTesting ? strings.settings.qbitTesting : strings.settings.testQbit}
                  </Button>
                  {qbitTestResult !== null && (
                    <p
                      role="status"
                      className={`text-sm font-medium ${qbitTestResult.ok ? 'text-success' : 'text-danger'}`}
                    >
                      {qbitTestResult.text}
                    </p>
                  )}
                </div>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 洗版 ---- */}
        <TabsContent value="upgrade">
          <Card title={strings.settings.tabs.upgrade}>
            <div className="divide-y divide-line">
              {numFieldRow('upgrade_threshold', strings.settings.upgradeThreshold)}
              {numFieldRow('upgrade_max_per_episode', strings.settings.upgradeMaxPerEpisode)}
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
              {numFieldRow('upgrade_skip_size_gb', strings.settings.upgradeSkipSizeGb)}
              {numFieldRow('mismatch_backfill_budget', strings.settings.mismatchBackfillBudget)}
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
          {/* 媒体库命名(批次一三开关):只影响之后的归档,不动已归档文件;
              示例预览随开关切换拼装(Movies/、Season 00、年份后缀三路示例) */}
          <Card title={strings.settings.namingSection}>
            <div className="divide-y divide-line">
              {switchRow('naming_movie_dir', strings.settings.namingMovieDir, strings.settings.namingMovieDirHint)}
              {switchRow(
                'naming_specials_s00',
                strings.settings.namingSpecialsS00,
                strings.settings.namingSpecialsHint,
              )}
              {switchRow(
                'naming_year_suffix',
                strings.settings.namingYearSuffix,
                strings.settings.namingYearSuffixHint,
              )}
              <SettingRow label={strings.settings.namingExample}>
                <div className="flex justify-end">
                  <span data-testid="naming-example" className="data-text text-sm text-ink-secondary">
                    {namingExamples.length > 0 ? namingExamples.join('；') : '—'}
                  </span>
                </div>
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
              {numFieldRow('rss_poll_interval_minutes', strings.settings.rssPollInterval)}
              {numFieldRow('rss_poll_jitter_pct', strings.settings.rssPollJitter)}
              {numFieldRow('download_poll_interval_s', strings.settings.downloadPollInterval)}
              {numFieldRow('download_max_retries', strings.settings.downloadMaxRetries)}
              {numFieldRow('collected_check_days', strings.settings.collectedCheckDays)}
              {numFieldRow(
                'pending_backlog_alert_threshold',
                strings.settings.pendingBacklogThreshold,
              )}
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
                <div className="flex justify-end">
                  <Button
                    loading={notifyTesting}
                    onClick={() => void runNotifyTest()}
                    className="hover:border-line! hover:bg-surface-2!"
                  >
                    {notifyTesting ? strings.settings.notifyTesting : strings.settings.testNotify}
                  </Button>
                </div>
              </SettingRow>
            </div>
          </Card>
        </TabsContent>

        {/* ---- 环境(只读 + API Token 本端注入,保持现状) ---- */}
        <TabsContent value="env">
          <Card
            title={strings.settings.environmentSection}
            actions={
              <div className="flex flex-col items-end gap-1">
                <Button
                  size="sm"
                  variant="secondary"
                  loading={checkingUpdate}
                  onClick={() => void runCheckUpdate()}
                >
                  {checkingUpdate ? strings.settings.checkingUpdate : strings.settings.checkUpdate}
                </Button>
                {updateResult !== null && (
                  <p role="status" className="text-xs text-ink-secondary">
                    {updateResult.url !== null ? (
                      <a
                        href={updateResult.url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-medium text-primary hover:text-primary-hover"
                      >
                        {updateResult.text} ↗
                      </a>
                    ) : (
                      updateResult.text
                    )}
                  </p>
                )}
              </div>
            }
          >
            {qbSavePath !== '' && qbSavePath !== (edit.download_path ?? base.download_path ?? '').trim() && (
              <p
                role="status"
                data-testid="qb-save-path-warning"
                className="mb-3 rounded-sm border border-line bg-surface-2 px-3 py-2 text-xs text-ink-secondary"
              >
                {strings.settings.qbSavePath}: {qbSavePath}。{strings.settings.qbSavePathMismatch}
              </p>
            )}
            <div className="divide-y divide-line">
              <SettingRow
                label={strings.settings.libraryPath}
                description={strings.settings.envPathHint}
                htmlFor="settings-library-path"
              >
                <Input
                  id="settings-library-path"
                  value={textValue('library_path')}
                  onChange={(e) => patch({ library_path: e.target.value })}
                  placeholder={base.library_path}
                  className="data-text"
                />
                {base.library_path_abs !== undefined && (
                  <span className="mt-1 block text-right text-xs text-ink-muted">
                    {base.library_path_abs}
                  </span>
                )}
              </SettingRow>
              <SettingRow
                label={strings.settings.downloadPath}
                description={strings.settings.envPathHint}
                htmlFor="settings-download-path"
              >
                <Input
                  id="settings-download-path"
                  value={textValue('download_path')}
                  onChange={(e) => patch({ download_path: e.target.value })}
                  placeholder={base.download_path}
                  className="data-text"
                />
                {base.download_path_abs !== undefined && (
                  <span className="mt-1 block text-right text-xs text-ink-muted">
                    {base.download_path_abs}
                  </span>
                )}
              </SettingRow>
              <SettingRow label={strings.settings.apiEndpoint}>
                <div className="flex justify-end">
                  <span className="data-text text-sm text-ink">
                    {base.api_host}:{base.api_port}
                  </span>
                </div>
              </SettingRow>
              <SettingRow label={strings.settings.sseHeartbeat}>
                <div className="flex justify-end">
                  <span className="data-text text-sm text-ink">{base.api_sse_heartbeat_s}s</span>
                </div>
              </SettingRow>
              <SettingRow label={strings.settings.sseReplay}>
                <div className="flex justify-end">
                  <span className="data-text text-sm text-ink">{base.api_sse_replay_limit}</span>
                </div>
              </SettingRow>
              <SettingRow label={strings.settings.apiToken} description={strings.settings.secretHint}>
                <div className="flex justify-end">
                  <Badge tone={base.has_api_token ? 'success' : 'neutral'} mark>
                    {base.has_api_token ? strings.settings.configured : strings.settings.notConfigured}
                  </Badge>
                </div>
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
      {/* 隐藏提交钮:jsdom/浏览器的表单隐式提交(default button)依赖它,回车即保存 */}
      <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
    </form>
  )
}
