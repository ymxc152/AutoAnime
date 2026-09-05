/*
 * Settings —— 下载器 / LLM 开关 / 自主权限三档 / 质量阈值表单,PUT /api/settings。
 * 编辑态 = 远端数据 + 局部 patch(无 effect 派生,脏态 = patch 非空);
 * 保存成功后清空 patch。
 */
import { useCallback, useState } from 'react'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { strings } from '../strings'
import {
  Button,
  Card,
  ErrorState,
  Input,
  PageTitle,
  SettingRow,
  Select,
  Skeleton,
  Switch,
} from '../components'
import type { AutonomyLevel, DownloadClient, SettingsDto } from '../api/types'

/** 自主权限三档选择(Soft Ink:选中态用 primary-light,拒绝浮动拇指分段控件) */
function AutonomyPicker({
  value,
  onChange,
}: {
  value: AutonomyLevel
  onChange: (v: AutonomyLevel) => void
}) {
  const options: Array<{ value: AutonomyLevel; label: string }> = [
    { value: 'low', label: strings.settings.autonomy.low },
    { value: 'medium', label: strings.settings.autonomy.medium },
    { value: 'high', label: strings.settings.autonomy.high },
  ]
  return (
    <div role="radiogroup" aria-label={strings.settings.autonomySection} className="flex flex-col gap-1">
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={`rounded-sm border px-2.5 py-1.5 text-left text-sm transition-colors ${
              active
                ? 'border-primary bg-primary-light font-medium text-ink'
                : 'border-line text-ink-secondary hover:bg-surface-2 hover:text-ink'
            }`}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function SectionField<T extends keyof SettingsDto>(props: {
  section: T
  edit: Partial<SettingsDto>
  data: SettingsDto
  onChange: (patch: Partial<SettingsDto>) => void
}): SettingsDto[T] {
  const edited = props.edit[props.section]
  return (edited !== undefined ? edited : props.data[props.section]) as SettingsDto[T]
}

export function SettingsPage() {
  const fetcher = useCallback(() => api.settings.get(), [])
  const { data, loading, error, reload } = useApi(fetcher)
  const [edit, setEdit] = useState<Partial<SettingsDto>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const patch = (partial: Partial<SettingsDto>): void => {
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

  const dirty = Object.keys(edit).length > 0
  const downloader = SectionField({ section: 'downloader', edit, data, onChange: patch })
  const llm = SectionField({ section: 'llm', edit, data, onChange: patch })
  const quality = SectionField({ section: 'quality', edit, data, onChange: patch })
  const autonomy = edit.autonomy ?? data.autonomy

  const save = async (): Promise<void> => {
    setSaving(true)
    setSaveError(null)
    try {
      await api.settings.update({ ...data, ...edit })
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

      <Card title={strings.settings.downloaderSection}>
        <div className="divide-y divide-line">
          <SettingRow label={strings.settings.client}>
            <Select
              value={downloader.client}
              onChange={(e) =>
                patch({ downloader: { ...downloader, client: e.target.value as DownloadClient } })
              }
              aria-label={strings.settings.client}
            >
              <option value="qbittorrent">{strings.settings.clientQbittorrent}</option>
              <option value="aria2">{strings.settings.clientAria2}</option>
            </Select>
          </SettingRow>
          <SettingRow label={strings.settings.downloaderUrl} htmlFor="settings-downloader-url">
            <Input
              id="settings-downloader-url"
              value={downloader.url}
              onChange={(e) => patch({ downloader: { ...downloader, url: e.target.value } })}
              className="data-text"
            />
          </SettingRow>
        </div>
      </Card>

      <Card title={strings.settings.llmSection}>
        <div className="divide-y divide-line">
          <SettingRow label={strings.settings.llmEnabled} description={strings.settings.llmEnabledHint}>
            <Switch
              checked={llm.enabled}
              onChange={(enabled) => patch({ llm: { ...llm, enabled } })}
              aria-label={strings.settings.llmEnabled}
            />
          </SettingRow>
          <SettingRow label={strings.settings.llmModel} htmlFor="settings-llm-model">
            <Input
              id="settings-llm-model"
              value={llm.model}
              onChange={(e) => patch({ llm: { ...llm, model: e.target.value } })}
              className="data-text"
            />
          </SettingRow>
        </div>
      </Card>

      <Card title={strings.settings.autonomySection} description={strings.settings.autonomyHint}>
        <AutonomyPicker value={autonomy} onChange={(v) => patch({ autonomy: v })} />
      </Card>

      <Card title={strings.settings.qualitySection}>
        <div className="divide-y divide-line">
          <SettingRow label={strings.settings.upgradeThreshold} htmlFor="settings-upgrade-threshold">
            <Input
              id="settings-upgrade-threshold"
              inputMode="numeric"
              value={String(quality.upgrade_threshold)}
              onChange={(e) =>
                patch({
                  quality: {
                    ...quality,
                    upgrade_threshold: Number(e.target.value.replace(/[^\d]/g, '') || 0),
                  },
                })
              }
            />
          </SettingRow>
          <SettingRow label={strings.settings.maxUpgrades} htmlFor="settings-max-upgrades">
            <Input
              id="settings-max-upgrades"
              inputMode="numeric"
              value={String(quality.max_upgrades_per_episode)}
              onChange={(e) =>
                patch({
                  quality: {
                    ...quality,
                    max_upgrades_per_episode: Number(e.target.value.replace(/[^\d]/g, '') || 0),
                  },
                })
              }
            />
          </SettingRow>
          <SettingRow label={strings.settings.copyPolicy}>
            <Select
              value={quality.copy_policy}
              onChange={(e) =>
                patch({
                  quality: {
                    ...quality,
                    copy_policy: e.target.value === 'strict' ? 'strict' : 'copy',
                  },
                })
              }
              aria-label={strings.settings.copyPolicy}
            >
              <option value="copy">{strings.settings.copyPolicyCopy}</option>
              <option value="strict">{strings.settings.copyPolicyStrict}</option>
            </Select>
          </SettingRow>
          <SettingRow label={strings.settings.skipSizeGb} htmlFor="settings-skip-size">
            <Input
              id="settings-skip-size"
              inputMode="numeric"
              value={String(quality.skip_size_gb)}
              onChange={(e) =>
                patch({
                  quality: {
                    ...quality,
                    skip_size_gb: Number(e.target.value.replace(/[^\d]/g, '') || 0),
                  },
                })
              }
            />
          </SettingRow>
        </div>
      </Card>
    </>
  )
}
