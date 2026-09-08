/*
 * SubscriptionDrawer —— P1-E 选番订阅抽屉(右侧 Drawer):
 * 展示条目详情(放送日期/集数/评分),单次 POST /api/subscriptions 完成
 * 「订阅 + 可选挂 RSS」(P0-B 一步订阅契约:bangumi_id 作 adopt 精确键,
 * rss_url/rss_token 同事务落 RssSource;rss_saved=false 且填了 RSS 时如实警告)。
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../api'
import { strings, t } from '../strings'
import { Badge, Button, Drawer, Field, Input } from './'
import { BangumiPoster } from './AnimeCard'
import { formatDate } from '../lib/views'
import type { BangumiItemDto } from '../api/types'

/** 抽屉展示标题:title_cn 缺失时用 title_jp 兜底(与 AnimeCard 同规则) */
function bangumiTitle(item: BangumiItemDto): string {
  return item.title_cn ?? item.title_jp
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-1.5 last:border-b-0">
      <span className="text-xs text-ink-secondary">{label}</span>
      <span className="text-xs text-ink">{value}</span>
    </div>
  )
}

export function SubscriptionDrawer({
  item,
  onClose,
  onSubscribed,
}: {
  item: BangumiItemDto
  onClose: () => void
  onSubscribed: () => void
}) {
  const [fansub, setFansub] = useState('')
  const [rssUrl, setRssUrl] = useState('')
  const [rssToken, setRssToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (attachRss: boolean): Promise<void> => {
    setSubmitting(true)
    setError(null)
    try {
      const attach = attachRss && rssUrl.trim() !== ''
      // 单次 POST(P0-B):bangumi_id 收编精确键;仅订阅路径 rss_url 缺省
      const created = await api.subscriptions.create({
        title_cn: item.title_cn ?? undefined,
        title_jp: item.title_jp,
        bangumi_id: String(item.subject_id),
        ...(item.eps !== null && item.eps > 0 ? { episode_count: item.eps } : {}),
        ...(fansub.trim() !== '' ? { fansub_pref: fansub.trim() } : {}),
        ...(attach
          ? {
              rss_url: rssUrl.trim(),
              ...(rssToken.trim() !== '' ? { rss_token: rssToken.trim() } : {}),
            }
          : {}),
      })
      // rss_saved=false 且填了 rss_url → 后端未挂成功,如实警告不谎报成功
      if (attach && created.rss_saved === false) {
        toast.warning(strings.uxfix.rssNotSaved)
      } else {
        toast.success(strings.uxfix.subscribeSuccess)
      }
      onClose()
      onSubscribed()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : strings.common.actionFailed)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Drawer
      open
      onClose={onClose}
      title={strings.subscriptions.addSubscription}
      subtitle={bangumiTitle(item)}
    >
      <div className="flex flex-col gap-4">
        <div className="flex gap-3">
          <div className="w-20 shrink-0">
            <BangumiPoster item={item} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 font-medium text-ink">{bangumiTitle(item)}</p>
            <p className="mt-0.5 line-clamp-1 text-xs text-ink-muted" title={item.title_jp}>
              {item.title_jp}
            </p>
            {item.rating !== null && (
              <span className="mt-1.5 inline-block">
                <Badge mark title={strings.uxfix.ratingLabel}>
                  <span className="data-text">
                    {t(strings.uxfix.ratingScore, { score: item.rating.toFixed(1) })}
                  </span>
                </Badge>
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col">
          <DetailRow label={strings.library.airDate} value={formatDate(item.air_date)} />
          <DetailRow
            label={strings.uxfix.ratingLabel}
            value={item.rating === null ? '—' : item.rating.toFixed(1)}
          />
          <DetailRow
            label={strings.library.episodes}
            value={item.eps === null ? '—' : item.eps}
          />
        </div>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            void submit(true)
          }}
        >
          <Field
            label={strings.subscriptions.fansubPref}
            description={strings.uxfix.fansubOptionalHint}
            htmlFor="season-drawer-fansub"
          >
            <Input
              id="season-drawer-fansub"
              value={fansub}
              onChange={(e) => setFansub(e.target.value)}
              placeholder={strings.subscriptions.fansubPlaceholder}
            />
          </Field>
          {/* RSS 地址按密钥对待(type=password):Mikan 地址常内嵌 token */}
          <Field label="RSS" description={strings.uxfix.drawerRssHint} htmlFor="season-drawer-rss">
            <Input
              id="season-drawer-rss"
              type="password"
              value={rssUrl}
              onChange={(e) => setRssUrl(e.target.value)}
              autoComplete="off"
              className="data-text"
            />
          </Field>
          <Field
            label={strings.rssSources.token}
            htmlFor="season-drawer-token"
          >
            <Input
              id="season-drawer-token"
              type="password"
              value={rssToken}
              onChange={(e) => setRssToken(e.target.value)}
              autoComplete="off"
            />
          </Field>
          {error !== null && (
            <p role="alert" className="text-xs text-danger">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {/* 「订阅并挂 RSS」仅在填写了 RSS 地址时可用;type=submit 走 form onSubmit */}
            <Button
              type="submit"
              variant="primary"
              loading={submitting && rssUrl.trim() !== ''}
              disabled={rssUrl.trim() === ''}
            >
              {strings.uxfix.subscribeAndAttach}
            </Button>
            <Button
              type="button"
              variant="secondary"
              loading={submitting && rssUrl.trim() === ''}
              onClick={() => void submit(false)}
            >
              {strings.uxfix.subscribeOnly}
            </Button>
          </div>
        </form>
      </div>
    </Drawer>
  )
}
