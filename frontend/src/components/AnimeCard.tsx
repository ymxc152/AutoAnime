/*
 * AnimeCard —— P1-E 季度选番卡片(Bangumi 规范化条目):
 * 封面(加载占位 Film 图标 / 失败回退标题首字)+ 标题(cn 优先 jp 兜底)+
 * 评分(实心 Star 图标 + 文本,12-UX:替代方块 mark 徽标,视觉不再像「■ 5.0 分」乱码)+
 * 放送月日 + 集数。卡片整体可点(选番 → 订阅抽屉);
 * 「在 Mikan 搜索」外链是卡片的兄弟节点(绝对定位右上角),天然不冒泡进卡片点击。
 */
import { useState } from 'react'
import { ExternalLink, Film, Star } from 'lucide-react'
import { strings, t } from '../strings'
import type { BangumiItemDto } from '../api/types'

/** 选番展示标题:title_cn 缺失时用 title_jp 兜底(后端 title_jp 必有) */
function bangumiTitle(item: BangumiItemDto): string {
  return item.title_cn ?? item.title_jp
}

/** Bangumi 封面:加载占位 + 加载失败回退(与 Library 海报降级语义一致) */
export function BangumiPoster({
  item,
  className,
}: {
  item: BangumiItemDto
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const box = className ?? 'aspect-[3/4] w-full'
  if (failed || item.image_url === null) {
    return (
      <div
        aria-hidden
        className={`flex ${box} shrink-0 items-center justify-center rounded-sm bg-surface-2 text-ink-muted`}
      >
        {failed ? (
          <span className="text-lg font-medium text-ink-secondary">{bangumiTitle(item).slice(0, 1)}</span>
        ) : (
          <Film className="h-6 w-6" />
        )}
      </div>
    )
  }
  return (
    <div className={`relative ${box} shrink-0`}>
      {!loaded && (
        <div
          aria-hidden
          className="absolute inset-0 flex items-center justify-center rounded-sm bg-surface-2 text-ink-muted"
        >
          <Film className="h-6 w-6" />
        </div>
      )}
      <img
        src={item.image_url}
        alt=""
        loading="lazy"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className={`h-full w-full rounded-sm border border-line object-cover transition-opacity duration-200 ${
          loaded ? 'opacity-100' : 'opacity-0'
        }`}
      />
    </div>
  )
}

/** ISO date(YYYY-MM-DD)→ 'MM-DD'(缺省回 '—') */
function monthDay(airDate: string | null): string {
  return airDate === null ? '—' : airDate.slice(5, 10)
}

export function AnimeCard({
  item,
  onSelect,
}: {
  item: BangumiItemDto
  onSelect: (item: BangumiItemDto) => void
}) {
  return (
    <div className="group relative" data-testid={`anime-card-${item.subject_id}`}>
      <button
        type="button"
        onClick={() => onSelect(item)}
        className="w-full overflow-hidden rounded-md border border-line bg-surface text-left shadow-soft-sm transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-soft-md"
      >
        <BangumiPoster item={item} />
        <div className="flex flex-col gap-1 p-2.5">
          <p className="line-clamp-1 font-medium text-ink" title={bangumiTitle(item)}>
            {bangumiTitle(item)}
          </p>
          <p className="line-clamp-1 text-xs text-ink-muted" title={item.title_jp}>
            {item.title_jp}
          </p>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-secondary">
            {item.rating !== null && (
              // 12-UX:评分 = 实心 Star(fill-current 跟随文字色)+ 分数文本;
              // 颜色用现有 warning token(amber,明暗主题自适应),不再用方块 mark 徽标
              <span className="inline-flex items-center gap-1" title={strings.uxfix.ratingLabel}>
                <Star aria-hidden className="h-3.5 w-3.5 fill-current text-warning" />
                <span className="data-text">
                  {t(strings.uxfix.ratingScore, { score: item.rating.toFixed(1) })}
                </span>
              </span>
            )}
            <span className="data-text">{monthDay(item.air_date)}</span>
            {item.eps !== null && item.eps > 0 && (
              <span className="data-text">{item.eps} {strings.library.episodes}</span>
            )}
          </div>
        </div>
      </button>
      {/* Mikan 外链:卡片兄弟节点,点击不会触发 onSelect;testid 供测试定位 */}
      <a
        href={item.mikan_search_url}
        target="_blank"
        rel="noreferrer"
        data-testid="anime-card-mikan-link"
        aria-label={`${strings.uxfix.mikanSearch} ${bangumiTitle(item)}`}
        className="absolute right-1.5 top-1.5 inline-flex items-center gap-1 rounded-sm bg-black/40 px-1.5 py-0.5 text-[11px] font-medium text-white opacity-60 transition-opacity duration-200 hover:opacity-100 focus-visible:opacity-100"
      >
        <ExternalLink aria-hidden className="h-3 w-3" />
        {strings.uxfix.mikanSearch}
      </a>
    </div>
  )
}
