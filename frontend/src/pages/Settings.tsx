/*
 * Settings —— 运行时开关 + 只读环境信息(对齐后端 SettingsOut/SettingsUpdateIn)。
 * 可覆写白名单(进程内,重启回 env/toml):dry_run/l2_enabled/llm_enabled/
 * llm_model/reference_enabled/reference_order;密钥只回 has_* 布尔。
 * quality/naming 段 v1 暂缺(无持久化 settings 表,依赖 E4),只给说明不造假数据。
 * dirty 时路由离开需确认(useBlocker 拦截侧栏点击 + 浏览器返回)。
 */
import { useCallback, useEffect, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import { api, ApiError, getApiToken, setApiToken } from '../api'
import { useApi } from '../hooks/useApi'
import { strings } from '../strings'
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Input,
  PageTitle,
  SettingRow,
  Skeleton,
  Switch,
} from '../components'
import type { SettingsDto, SettingsUpdateBody } from '../api/types'

/** 逗号分隔串 → 参考源数组(split/trim/去空) */
function parseOrder(raw: string): string[] {
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

export function SettingsPage() {
  const fetcher = useCallback(() => api.settings.get(), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [edit, setEdit] = useState<SettingsUpdateBody>({})
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

  const patch = (partial: SettingsUpdateBody): void => {
    setEdit((prev) => ({ ...prev, ...partial }))
  }

  // dirty 只依赖 edit state 与未提交草稿,可无条件在 hooks 区域计算
  // (载入前 data 为 null,基线按 edit/空数组兜底;真正使用 dirty 时数据已就绪)
  const dirty =
    Object.keys(edit).length > 0 ||
    (orderDraft !== null &&
      orderDraft !== (edit.reference_order ?? savedSnapshot?.reference_order ?? data?.reference_order ?? []).join(','))

  // 未保存更改时,路由离开需确认(useBlocker 拦截侧栏点击 + 浏览器返回)
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  )
  useEffect(() => {
    if (blocker.state === 'blocked') {
      const leave = window.confirm('有未保存的更改,确定离开吗?')
      if (leave) blocker.proceed()
      else blocker.reset()
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
  const dryRun = edit.dry_run ?? base.dry_run
  const l2Enabled = edit.l2_enabled ?? base.l2_enabled
  const llmEnabled = edit.llm_enabled ?? base.llm_enabled
  const llmModel = edit.llm_model ?? base.llm_model ?? ''
  const referenceEnabled = edit.reference_enabled ?? base.reference_enabled
  const canonicalOrder = (edit.reference_order ?? base.reference_order).join(',')
  // 展示值:编辑草稿优先(保真原文,末尾逗号不丢),否则回显当前基线
  const referenceOrder = orderDraft ?? canonicalOrder

  /** blur 提交:归一化草稿写回 edit;与基线一致时不制造脏态 */
  const commitOrder = (): void => {
    if (orderDraft === null) return
    const parsed = parseOrder(orderDraft)
    if (parsed.join(',') !== canonicalOrder) {
      patch({ reference_order: parsed })
    }
    setOrderDraft(parsed.join(',') === canonicalOrder ? null : parsed.join(','))
  }

  const save = async (): Promise<void> => {
    setSaving(true)
    setSaveError(null)
    try {
      // 保存即提交:草稿尚未 blur 也归一化写入本次请求(与 blur 提交同语义)
      const payload: SettingsUpdateBody =
        orderDraft !== null ? { ...edit, reference_order: parseOrder(orderDraft) } : edit
      const savedSettings = await api.settings.update(payload)
      setSavedSnapshot(savedSettings)
      setEdit({})
      setOrderDraft(null)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2500)
    } catch (cause) {
      setSaveError(cause instanceof ApiError ? cause.message : strings.settings.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  /** 保存/清除 API Token(写 localStorage,即时生效需刷新);留空保存 = 清除 */
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

      <Card title={strings.settings.runtimeSection}>
        <div className="divide-y divide-line">
          <SettingRow label={strings.settings.dryRun} description={strings.settings.dryRunHint}>
            <Switch
              checked={dryRun}
              onChange={(checked) => patch({ dry_run: checked })}
              aria-label={strings.settings.dryRun}
            />
          </SettingRow>
          <SettingRow label={strings.settings.l2Enabled} description={strings.settings.l2EnabledHint}>
            <Switch
              checked={l2Enabled}
              onChange={(checked) => patch({ l2_enabled: checked })}
              aria-label={strings.settings.l2Enabled}
            />
          </SettingRow>
          <SettingRow label={strings.settings.llmEnabled} description={strings.settings.llmEnabledHint}>
            <Switch
              checked={llmEnabled}
              onChange={(checked) => patch({ llm_enabled: checked })}
              aria-label={strings.settings.llmEnabled}
            />
          </SettingRow>
          <SettingRow label={strings.settings.llmModel} htmlFor="settings-llm-model">
            <Input
              id="settings-llm-model"
              value={llmModel}
              onChange={(e) => patch({ llm_model: e.target.value })}
              className="data-text"
            />
          </SettingRow>
          <SettingRow
            label={strings.settings.referenceEnabled}
            description={strings.settings.referenceEnabledHint}
          >
            <Switch
              checked={referenceEnabled}
              onChange={(checked) => patch({ reference_enabled: checked })}
              aria-label={strings.settings.referenceEnabled}
            />
          </SettingRow>
          <SettingRow
            label={strings.settings.referenceOrder}
            description={strings.settings.referenceOrderHint}
            htmlFor="settings-reference-order"
          >
            <Input
              id="settings-reference-order"
              value={referenceOrder}
              onChange={(e) => setOrderDraft(e.target.value)}
              onBlur={commitOrder}
              className="data-text"
            />
          </SettingRow>
        </div>
      </Card>

      <Card title={strings.settings.environmentSection}>
        <div className="divide-y divide-line">
          <SettingRow label={strings.settings.libraryPath}>
            <span className="data-text text-sm text-ink">{base.library_path}</span>
          </SettingRow>
          <SettingRow label={strings.settings.downloadPath}>
            <span className="data-text text-sm text-ink">{base.download_path}</span>
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
          <SettingRow label={strings.settings.llmApiKey} description={strings.settings.secretHint}>
            <Badge tone={base.has_llm_api_key ? 'success' : 'neutral'} mark>
              {base.has_llm_api_key ? strings.settings.configured : strings.settings.notConfigured}
            </Badge>
          </SettingRow>
        </div>
      </Card>

      <Card title={strings.settings.qualitySection} description={strings.settings.e4Hint}>
        <p className="px-4 py-3 text-sm text-ink-secondary">{strings.settings.e4Unavailable}</p>
      </Card>
    </>
  )
}
