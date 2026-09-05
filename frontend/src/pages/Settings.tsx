/*
 * Settings —— 运行时开关 + 只读环境信息(对齐后端 SettingsOut/SettingsUpdateIn)。
 * 可覆写白名单(进程内,重启回 env/toml):dry_run/l2_enabled/llm_enabled/
 * llm_model/reference_enabled/reference_order;密钥只回 has_* 布尔。
 * quality/naming 段 v1 暂缺(无持久化 settings 表,依赖 E4),只给说明不造假数据。
 */
import { useCallback, useState } from 'react'
import { api, ApiError } from '../api'
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

export function SettingsPage() {
  const fetcher = useCallback(() => api.settings.get(), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [edit, setEdit] = useState<SettingsUpdateBody>({})
  // 保存成功后立即以服务端返回值为展示基线(useApi 的 data 要等 reload 才更新)
  const [savedSnapshot, setSavedSnapshot] = useState<SettingsDto | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const patch = (partial: SettingsUpdateBody): void => {
    setEdit((prev) => ({ ...prev, ...partial }))
  }

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
  const dirty = Object.keys(edit).length > 0
  const dryRun = edit.dry_run ?? base.dry_run
  const l2Enabled = edit.l2_enabled ?? base.l2_enabled
  const llmEnabled = edit.llm_enabled ?? base.llm_enabled
  const llmModel = edit.llm_model ?? base.llm_model ?? ''
  const referenceEnabled = edit.reference_enabled ?? base.reference_enabled
  const referenceOrder = (edit.reference_order ?? base.reference_order).join(',')

  const save = async (): Promise<void> => {
    setSaving(true)
    setSaveError(null)
    try {
      const savedSettings = await api.settings.update(edit)
      setSavedSnapshot(savedSettings)
      setEdit({})
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2500)
    } catch (cause) {
      setSaveError(cause instanceof ApiError ? cause.message : strings.settings.saveFailed)
    } finally {
      setSaving(false)
    }
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
              onChange={(e) =>
                patch({
                  reference_order: e.target.value
                    .split(',')
                    .map((item) => item.trim())
                    .filter((item) => item !== ''),
                })
              }
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
