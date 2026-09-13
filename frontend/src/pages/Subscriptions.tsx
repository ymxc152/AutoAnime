/*
 * Subscriptions —— 追番(P1-E 信息架构重构:双 Tab)。
 * Tab1「季度选番」(默认):当季走 GET /api/season-calendar,历史季走
 * GET /api/season-browse(近 6 年 × 4 季;degraded=true 显示降级提示);
 * 卡片点开 SubscriptionDrawer,单次 POST /api/subscriptions 完成
 * 订阅 + 可选挂 RSS(P0-B 一步订阅,bangumi_id 作 adopt 精确键)。
 * Tab2「我的订阅」:既有订阅列表/表单整体迁入(添加/编辑/删除全保留;
 * 12-IA 弹窗化:添加订阅改为按钮 + 居中 Dialog,编辑仍走右侧 Drawer)。
 */
import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { CirclePlus, ExternalLink, Tv } from 'lucide-react'
import { api, ApiError } from '../api'
import { useApi } from '../hooks/useApi'
import { useReloadOnMessages } from '../hooks/useReloadOnEvent'
import { strings, t } from '../strings'
import {
  AnimeCard,
  Badge,
  Button,
  Card,
  Drawer,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageTitle,
  ProgressBar,
  Select,
  Skeleton,
  StatusDot,
  SubscriptionDrawer,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../components'
import { mediaTypeLabel, seasonStateView, subscriptionStatusLabel } from '../lib/views'
// 12-IA 弹窗化:添加订阅常驻卡片 → 按钮 + 居中 Dialog(编辑仍走右侧 Drawer)
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { BangumiItemDto, SeasonName, SubscriptionDto } from '../api/types'

const MIKAN_URL = 'https://mikanani.me'

/** 季节切换条可选项:近 6 年(含当年)× 4 季 */
const SEASON_NAMES: SeasonName[] = ['winter', 'spring', 'summer', 'fall']
const YEAR_COUNT = 6

/** 月(0-11)→ 季名(12/1/2=winter,3-5=spring,6-8=summer,9-11=fall) */
function monthToSeason(month: number): SeasonName {
  if (month >= 3 && month <= 5) return 'spring'
  if (month >= 6 && month <= 8) return 'summer'
  if (month >= 9) return 'fall'
  return 'winter'
}

/** 季节切换条数据源:当季 = calendar;历史季 = browse(区分走哪个端点) */
type SeasonScope = { kind: 'current' } | { kind: 'browse'; year: number; season: SeasonName }

/* ---------- Tab1:季度选番 ---------- */

function SeasonBrowseTab({ onSubscribed }: { onSubscribed: () => void }) {
  const now = new Date()
  const years = Array.from({ length: YEAR_COUNT }, (_, i) => now.getFullYear() - i)
  const [scope, setScope] = useState<SeasonScope>({ kind: 'current' })
  // Select 受控值(与 scope 分离:未切历史季时仅作草稿)
  const [yearInput, setYearInput] = useState(String(now.getFullYear()))
  const [seasonInput, setSeasonInput] = useState<SeasonName>(monthToSeason(now.getMonth()))

  const fetcher = useCallback(
    () =>
      scope.kind === 'current'
        ? api.seasonCalendar.get()
        : api.seasonBrowse.get({ year: scope.year, season: scope.season }),
    [scope],
  )
  const { data, loading, error, reload } = useApi(fetcher)

  const [selected, setSelected] = useState<BangumiItemDto | null>(null)

  const isCurrent = scope.kind === 'current'
  const items = data?.items ?? []

  return (
    <div className="flex flex-col gap-3">
      {/* 季节切换条:当季 + 年份/季节 Select */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={isCurrent ? 'primary' : 'secondary'}
          onClick={() => setScope({ kind: 'current' })}
        >
          {strings.uxfix.seasonCurrent}
        </Button>
        <Select
          aria-label={strings.uxfix.seasonYearLabel}
          data-testid="season-year-select"
          className="w-28"
          value={yearInput}
          onChange={(e) => {
            setYearInput(e.target.value)
            setScope({ kind: 'browse', year: Number(e.target.value), season: seasonInput })
          }}
        >
          {years.map((year) => (
            <option key={year} value={String(year)}>
              {year}
            </option>
          ))}
        </Select>
        <Select
          aria-label={strings.uxfix.seasonNameLabel}
          data-testid="season-name-select"
          className="w-28"
          value={seasonInput}
          onChange={(e) => {
            const season = e.target.value as SeasonName
            setSeasonInput(season)
            setScope({ kind: 'browse', year: Number(yearInput), season })
          }}
        >
          {SEASON_NAMES.map((season) => (
            <option key={season} value={season}>
              {strings.uxfix.seasonNames[season]}
            </option>
          ))}
        </Select>
      </div>

      {error !== null ? (
        <ErrorState message={error} onRetry={reload} />
      ) : loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-64" />
          ))}
        </div>
      ) : data?.degraded ? (
        // 网关降级:items 为空,如实提示不伪装成「无番」
        <Card>
          <p role="status" data-testid="season-degraded" className="py-2 text-sm text-ink-secondary">
            {strings.uxfix.seasonDegraded}
          </p>
        </Card>
      ) : items.length === 0 ? (
        <EmptyState title={strings.uxfix.seasonGridEmpty} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="season-grid">
          {items.map((item) => (
            <AnimeCard key={item.subject_id} item={item} onSelect={setSelected} />
          ))}
        </div>
      )}

      {selected !== null && (
        <SubscriptionDrawer
          item={selected}
          onClose={() => setSelected(null)}
          onSubscribed={onSubscribed}
        />
      )}
    </div>
  )
}

/* ---------- Tab2:我的订阅(既有功能整体迁入) ---------- */

function subscriptionTitle(sub: SubscriptionDto): string {
  return sub.title_cn ?? sub.title_romaji ?? sub.title_jp ?? `#${sub.id}`
}

/** 状态徽标语义色:active=连载(success)/paused=暂停(warning)/finished=完结(neutral) */
function subscriptionStatusTone(status: string): 'success' | 'warning' | 'neutral' {
  if (status === 'active') return 'success'
  if (status === 'paused') return 'warning'
  return 'neutral'
}

/**
 * 12-UX 卡片徽章与季行同口径:订阅 active 但所追季全部未放送(upcoming)时,
 * 徽章不得宣称「连载中」,与季行「未放送」一致;其余情况
 * (已有放送内容的 active / paused / finished)保持原状态逻辑不变。
 */
function subscriptionBadgeView(sub: SubscriptionDto): {
  label: string
  tone: 'success' | 'warning' | 'neutral'
} {
  const allUpcoming =
    sub.status === 'active' &&
    sub.seasons.length > 0 &&
    sub.seasons.every((season) => season.status === 'upcoming')
  if (allUpcoming) {
    return { label: strings.library.seasonState.upcoming, tone: 'neutral' }
  }
  return { label: subscriptionStatusLabel(sub.status), tone: subscriptionStatusTone(sub.status) }
}

/**
 * 添加订阅弹窗(12-IA 弹窗化:原常驻 AddSubscriptionForm 卡片整体迁入,
 * 字段/校验/mock 行为不变,仅容器从卡片变居中 Dialog;提交成功后关闭并刷新)。
 */
function AddSubscriptionDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const [title, setTitle] = useState('')
  const [seasonNumber, setSeasonNumber] = useState('1')
  const [episodeCount, setEpisodeCount] = useState('')
  const [fansub, setFansub] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (): Promise<void> => {
    if (title.trim() === '') {
      setError(strings.subscriptions.titleRequired)
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await api.subscriptions.create({
        title_cn: title.trim(),
        season_number: seasonNumber === '' ? undefined : Number(seasonNumber),
        // episode_count 留空 = 只建 Series/Season,不预生成集表
        ...(episodeCount !== '' ? { episode_count: Number(episodeCount) } : {}),
        ...(fansub.trim() !== '' ? { fansub_pref: fansub.trim() } : {}),
      })
      setTitle('')
      setSeasonNumber('1')
      setEpisodeCount('')
      setFansub('')
      onDone()
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.subscriptions.addFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="inline-flex items-center gap-1.5">
            <CirclePlus aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
            {strings.subscriptions.addSubscription}
          </DialogTitle>
          <DialogDescription>{strings.subscriptions.rssHint}</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <Field label={strings.subscriptions.titleLabel} error={error} htmlFor="sub-title">
            <Input
              id="sub-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={strings.subscriptions.titlePlaceholder}
              invalid={error !== null}
              className="data-text"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={strings.subscriptions.seasonNumber} htmlFor="sub-season">
              <Input
                id="sub-season"
                inputMode="numeric"
                value={seasonNumber}
                onChange={(e) => setSeasonNumber(e.target.value.replace(/[^\d]/g, ''))}
                className="data-text"
              />
            </Field>
            <Field label={strings.subscriptions.episodeCount} htmlFor="sub-episodes">
              <Input
                id="sub-episodes"
                inputMode="numeric"
                value={episodeCount}
                onChange={(e) => setEpisodeCount(e.target.value.replace(/[^\d]/g, ''))}
                placeholder={strings.subscriptions.episodeCountPlaceholder}
                className="data-text"
              />
            </Field>
          </div>
          <Field label={strings.subscriptions.fansubPref} htmlFor="sub-fansub">
            <Input
              id="sub-fansub"
              value={fansub}
              onChange={(e) => setFansub(e.target.value)}
              placeholder={strings.subscriptions.fansubPlaceholder}
            />
          </Field>
          <div className="flex items-center justify-between gap-2">
            <Button type="submit" variant="primary" loading={submitting}>
              {strings.subscriptions.submitAdd}
            </Button>
            <a
              href={MIKAN_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-hover"
            >
              {strings.subscriptions.mikanEntry}
              <ExternalLink aria-hidden className="h-3 w-3" />
            </a>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditSubscriptionDrawer({
  sub,
  onDone,
  onClose,
}: {
  sub: SubscriptionDto
  onDone: () => void
  onClose: () => void
}) {
  const [status, setStatus] = useState(sub.status)
  const [fansub, setFansub] = useState(sub.fansub_pref ?? '')
  const [quality, setQuality] = useState(sub.quality_pref ?? '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (): Promise<void> => {
    setSubmitting(true)
    setError(null)
    try {
      // 空字符串显式提交为 null：编辑抽屉里“清空并保存”就是清除偏好。
      await api.subscriptions.update(sub.id, {
        status,
        fansub_pref: fansub.trim() === '' ? null : fansub.trim(),
        quality_pref: quality.trim() === '' ? null : quality.trim(),
      })
      onDone()
      onClose()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Drawer open onClose={onClose} title={strings.subscriptions.editTitle} subtitle={subscriptionTitle(sub)}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <Field label={strings.subscriptions.status} htmlFor="subscription-edit-status">
          <Select
            id="subscription-edit-status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="active">{strings.subscriptions.statusActive}</option>
            <option value="paused">{strings.subscriptions.statusPaused}</option>
            <option value="finished">{strings.subscriptions.statusFinished}</option>
          </Select>
        </Field>
        <Field
          label={strings.subscriptions.fansubPref}
          description={strings.subscriptions.optionalClearHint}
          htmlFor="subscription-edit-fansub"
        >
          <Input
            id="subscription-edit-fansub"
            value={fansub}
            onChange={(e) => setFansub(e.target.value)}
            placeholder={strings.subscriptions.fansubPlaceholder}
          />
        </Field>
        <Field
          label={strings.subscriptions.qualityPref}
          description={strings.subscriptions.optionalClearHint}
          htmlFor="subscription-edit-quality"
        >
          <Input
            id="subscription-edit-quality"
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
          />
        </Field>
        {error !== null && (
          // 12-UX:校验错误文字升级 text-sm font-medium,深色模式下 text-xs 偏细难读
          <p role="alert" className="text-sm font-medium text-danger">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" loading={submitting}>
            {strings.common.save}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            {strings.common.cancel}
          </Button>
        </div>
      </form>
    </Drawer>
  )
}

function SubscriptionRow({
  sub,
  onEdit,
  onRemove,
  removing,
}: {
  sub: SubscriptionDto
  onEdit: (sub: SubscriptionDto) => void
  onRemove: (id: number) => void
  removing: boolean
}) {
  // P1-UX:每行给「在 Mikan 搜索」小外链(触屏用户也可直达),按标题拼接搜索地址
  const mikanSearchUrl = `${MIKAN_URL}/Home/Search?searchstr=${encodeURIComponent(subscriptionTitle(sub))}`
  // 12-UX:徽章与季行同口径(全未放送时显示「未放送」而非「连载中」)
  const badge = subscriptionBadgeView(sub)
  return (
    <div className="flex flex-col gap-2 border-b border-line px-4 py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{subscriptionTitle(sub)}</span>
        <Badge>{mediaTypeLabel(sub.media_type)}</Badge>
        <Badge tone={badge.tone}>{badge.label}</Badge>
        <Badge>{sub.fansub_pref ?? strings.subscriptions.noFansub}</Badge>
        <span className="ml-auto flex items-center gap-1.5">
          {/* P1-UX:「在 Mikan 搜索」小外链(新窗口打开,不冒泡) */}
          <a
            href={mikanSearchUrl}
            target="_blank"
            rel="noreferrer"
            data-testid={`mikan-search-${sub.id}`}
            aria-label={`${strings.uxfix.mikanSearch} ${subscriptionTitle(sub)}`}
            className="inline-flex items-center gap-1 text-xs font-medium text-ink-secondary transition-colors hover:text-ink"
          >
            <ExternalLink aria-hidden className="h-3 w-3" />
            {strings.uxfix.mikanSearch}
          </a>
          <Button size="sm" variant="secondary" onClick={() => onEdit(sub)}>
            {strings.subscriptions.edit}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            loading={removing}
            onClick={() => onRemove(sub.id)}
          >
            {strings.common.remove}
          </Button>
        </span>
      </div>
      {sub.seasons.length === 0 ? (
        <p className="text-xs text-ink-secondary">{strings.subscriptions.noSeasons}</p>
      ) : (
        sub.seasons.map((season) => {
          const view = seasonStateView(season.status)
          // P1-UX:未放送/集数总数为 0 时,「已归档 0/0 集 缺 0 集」无信息量 → 如实标注
          const notAired = season.status === 'upcoming'
          const noEpisodes = notAired || season.episodes_total === 0
          const progress =
            season.episodes_total > 0
              ? season.episodes_organized / season.episodes_total
              : 0
          return (
            <div key={season.season_id} className="flex flex-col gap-1.5">
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
                <StatusDot tone={view.tone} size={7} />
                <span className="data-text text-ink">
                  {t(strings.library.seasonN, { n: season.number })}
                </span>
                <span>{view.label}</span>
                {noEpisodes ? (
                  <span className="data-text">
                    {notAired ? strings.uxfix.notAired : strings.uxfix.episodesUnknown}
                  </span>
                ) : (
                  <>
                    <span className="data-text">
                      {t(strings.subscriptions.organizedOfTotal, {
                        organized: season.episodes_organized,
                        total: season.episodes_total,
                      })}
                    </span>
                    <span className="data-text">
                      {t(strings.subscriptions.missingCount, { count: season.episodes_missing })}
                    </span>
                  </>
                )}
                <span className="data-text">
                  {t(strings.subscriptions.rssCount, { count: season.rss_sources })}
                </span>
              </div>
              <ProgressBar value={progress} tone={view.tone === 'success' ? 'success' : 'primary'} />
            </div>
          )
        })
      )}
    </div>
  )
}

function MySubscriptionsTab() {
  const fetcher = useCallback(() => api.subscriptions.list({ limit: 100 }), [])
  const { data, loading, error, reload } = useApi(fetcher)
  useReloadOnMessages(reload, [
    'subscription.created',
    'subscription.updated',
    'subscription.deleted',
    'episode.gap',
  ])
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)
  const [editingSub, setEditingSub] = useState<SubscriptionDto | null>(null)
  // 12-IA 弹窗化:添加订阅弹窗开关
  const [adding, setAdding] = useState(false)
  // 取消订阅失败不再静默(A2):复用页面级 role="alert" 错误条
  const [actionError, setActionError] = useState<string | null>(null)

  const subs = data?.items ?? []

  const remove = async (id: number): Promise<void> => {
    const sub = subs.find((s) => s.id === id)
    if (sub !== undefined && confirmId !== id) {
      setConfirmId(id)
      return
    }
    setRemovingId(id)
    setActionError(null)
    try {
      await api.subscriptions.remove(id)
      setConfirmId(null)
      reload()
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : strings.subscriptions.removeFailed)
    } finally {
      setRemovingId(null)
    }
  }

  return (
    <>
      {/* 12-IA 弹窗化:顶部一行操作区 —— 「添加订阅」主按钮 + RSS 源高级管理入口 */}
      <div className="flex items-center justify-between">
        <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
          <CirclePlus aria-hidden className="h-3.5 w-3.5" />
          {strings.subscriptions.addSubscription}
        </Button>
        <Link
          to="/rss-sources"
          data-testid="manage-rss-link"
          className="inline-flex h-7 w-fit items-center gap-1.5 rounded-sm px-2 text-xs font-medium text-ink-secondary transition-colors duration-[var(--ink-transition-fast)] hover:bg-surface-2 hover:text-ink"
        >
          {strings.uxfix.manageRss}
        </Link>
      </div>

      {actionError !== null && (
        <div role="alert" className="rounded-md border border-line px-3 py-2 text-sm text-ink-secondary">
          <strong className="mr-1.5 text-danger">{strings.common.actionFailed}</strong>
          {actionError}
        </div>
      )}

      <Card flush>
        {error !== null ? (
          <div className="p-4">
            <ErrorState message={error} onRetry={reload} />
          </div>
        ) : loading ? (
          <div className="flex flex-col gap-2 p-4">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        ) : subs.length === 0 ? (
          <div className="p-4">
            <Tv aria-hidden className="mb-2 h-8 w-8 text-ink-muted" />
            {/* 12-IA 弹窗化:空态引导按钮指向添加订阅弹窗 */}
            <EmptyState
              title={strings.subscriptions.empty}
              action={
                <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
                  {strings.subscriptions.addSubscription}
                </Button>
              }
            />
          </div>
        ) : (
          subs.map((sub) => (
            <SubscriptionRow
              key={sub.id}
              sub={sub}
              onEdit={setEditingSub}
              onRemove={(id) => void remove(id)}
              removing={removingId === sub.id}
            />
          ))
        )}
        {confirmId !== null && (
          <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-2.5">
            <span className="text-xs text-ink-secondary">
              {t(strings.subscriptions.removeConfirm, {
                title: subscriptionTitle(subs.find((s) => s.id === confirmId)!),
              })}
            </span>
            <span className="flex gap-2">
              <Button size="sm" variant="danger" onClick={() => void remove(confirmId)}>
                {strings.common.confirm}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                {strings.common.cancel}
              </Button>
            </span>
          </div>
        )}
      </Card>

      {/* 12-IA 弹窗化:添加订阅弹窗(原常驻表单卡迁入);key 随开关重挂载 → 重开不留旧输入/旧报错 */}
      <AddSubscriptionDialog
        key={adding ? 'adding-open' : 'adding-closed'}
        open={adding}
        onOpenChange={setAdding}
        onDone={reload}
      />

      {editingSub !== null && (
        <EditSubscriptionDrawer
          sub={editingSub}
          onDone={reload}
          onClose={() => setEditingSub(null)}
        />
      )}
    </>
  )
}

/* ---------- 页面骨架:双 Tab ---------- */

type SubscriptionTab = 'season' | 'mine'

export function SubscriptionsPage() {
  // 默认落在「季度选番」:选番是一切的起点(12-IA)
  const [tab, setTab] = useState<SubscriptionTab>('season')

  return (
    <>
      <PageTitle title={strings.subscriptions.title} />
      <Tabs value={tab} onValueChange={(value) => setTab(value as SubscriptionTab)}>
        <TabsList data-testid="subscriptions-tabs">
          <TabsTrigger value="season">{strings.uxfix.seasonBrowse}</TabsTrigger>
          <TabsTrigger value="mine">{strings.uxfix.mySubscriptions}</TabsTrigger>
        </TabsList>
        <TabsContent value="season">
          <SeasonBrowseTab onSubscribed={() => setTab('mine')} />
        </TabsContent>
        <TabsContent value="mine">
          <MySubscriptionsTab />
        </TabsContent>
      </Tabs>
    </>
  )
}
