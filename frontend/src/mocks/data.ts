/*
 * Mock fixtures —— E2 未合并期间的本地假数据(Plan §5.5 允许)。
 * 数据形状与 src/api/types.ts 契约严格一致,兼作契约类型的"可运行文档"。
 */
import type {
  AuditDto,
  EpisodeDto,
  Metrics,
  PendingItemDto,
  RssSourceDto,
  SeriesDto,
  SettingsDto,
  SubscriptionDto,
} from '../api/types'

const DAY_MS = 24 * 60 * 60 * 1000

function isoDaysAgo(days: number, hour = 12): string {
  const d = new Date(Date.now() - days * DAY_MS)
  d.setHours(hour, 30, 0, 0)
  return d.toISOString()
}

function dateDaysAgo(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10)
}

function makeEpisodes(
  seriesId: number,
  seasonId: number,
  count: number,
  statePlan: (n: number) => Pick<EpisodeDto, 'state' | 'quality_score' | 'upgraded_count' | 'file_path'>,
): EpisodeDto[] {
  return Array.from({ length: count }, (_, i) => {
    const n = i + 1
    const plan = statePlan(n)
    return {
      id: seasonId * 100 + n,
      series_id: seriesId,
      season_id: seasonId,
      number: n,
      state: plan.state,
      quality_score: plan.quality_score,
      upgraded_count: plan.upgraded_count,
      air_date: new Date(Date.now() - (count - n) * 7 * DAY_MS).toISOString().slice(0, 10),
      file_path: plan.file_path,
    }
  })
}

// ---- series ----

export const mockSeries: SeriesDto[] = [
  {
    id: 1,
    title_cn: '葬送的芙莉莲',
    title_jp: '葬送のフリーレン',
    title_romaji: 'Sousou no Frieren',
    media_type: 'tv',
    status: 'active',
    bangumi_id: '401831',
    tmdb_id: '209867',
    fansub_pref: '栀次元字幕组',
    quality_pref: '1080p',
    seasons: [
      {
        id: 1,
        series_id: 1,
        number: 1,
        state: 'collected',
        episodes: makeEpisodes(1, 1, 28, (n) => ({
          state: n % 7 === 0 ? 'upgraded' : 'organized',
          quality_score: n % 7 === 0 ? 11 : 9,
          upgraded_count: n % 7 === 0 ? 1 : 0,
          file_path: `/library/葬送的芙莉莲/Season 1/葬送的芙莉莲 - S01E${String(n).padStart(2, '0')}.1080p.mkv`,
        })),
      },
    ],
  },
  {
    id: 2,
    title_cn: '药屋少女的呢喃',
    title_jp: '薬屋のひとりごと',
    title_romaji: 'Kusuriya no Hitorigoto',
    media_type: 'tv',
    status: 'active',
    bangumi_id: '369883',
    tmdb_id: '230573',
    fansub_pref: 'Kamigakari',
    quality_pref: '1080p',
    seasons: [
      {
        id: 2,
        series_id: 2,
        number: 2,
        state: 'airing',
        episodes: makeEpisodes(2, 2, 24, (n) =>
          n <= 15
            ? { state: 'organized', quality_score: 9, upgraded_count: 0, file_path: `/library/药屋少女的呢喃/Season 2/药屋少女的呢喃 - S02E${String(n).padStart(2, '0')}.1080p.mkv` }
            : n === 16
              ? { state: 'downloading', quality_score: null, upgraded_count: 0, file_path: null }
              : { state: 'missing', quality_score: null, upgraded_count: 0, file_path: null },
        ),
      },
    ],
  },
  {
    id: 3,
    title_cn: '我推的孩子',
    title_jp: '推しの子',
    title_romaji: 'Oshi no Ko',
    media_type: 'tv',
    status: 'active',
    bangumi_id: '351989',
    tmdb_id: '119060',
    fansub_pref: null,
    quality_pref: '1080p',
    seasons: [
      {
        id: 3,
        series_id: 3,
        number: 1,
        state: 'ended',
        episodes: makeEpisodes(3, 3, 11, () => ({
          state: 'organized',
          quality_score: 8,
          upgraded_count: 0,
          file_path: '/library/我推的孩子/Season 1/placeholder.mkv',
        })),
      },
      {
        id: 4,
        series_id: 3,
        number: 2,
        state: 'airing',
        episodes: makeEpisodes(3, 4, 13, (n) =>
          n <= 4
            ? { state: 'organized', quality_score: 9, upgraded_count: 0, file_path: '/library/我推的孩子/Season 2/placeholder.mkv' }
            : { state: 'missing', quality_score: null, upgraded_count: 0, file_path: null },
        ),
      },
    ],
  },
  {
    id: 4,
    title_cn: '间谍过家家',
    title_jp: 'SPY×FAMILY',
    title_romaji: 'Spy x Family',
    media_type: 'tv',
    status: 'active',
    bangumi_id: '365277',
    tmdb_id: '122770',
    fansub_pref: '幺幺字幕组',
    quality_pref: '720p',
    seasons: [
      {
        id: 5,
        series_id: 4,
        number: 1,
        state: 'collected',
        episodes: makeEpisodes(4, 5, 12, () => ({
          state: 'organized',
          quality_score: 7,
          upgraded_count: 0,
          file_path: '/library/间谍过家家/Season 1/placeholder.mkv',
        })),
      },
    ],
  },
  {
    id: 5,
    title_cn: '迷宫饭',
    title_jp: 'ダンジョン飯',
    title_romaji: 'Dungeon Meshi',
    media_type: 'tv',
    status: 'active',
    bangumi_id: '382241',
    tmdb_id: '209866',
    fansub_pref: 'NC-Raws',
    quality_pref: '1080p',
    seasons: [
      {
        id: 6,
        series_id: 5,
        number: 1,
        state: 'airing',
        episodes: makeEpisodes(5, 6, 24, (n) =>
          n <= 20
            ? { state: 'organized', quality_score: n % 5 === 0 ? 10 : 9, upgraded_count: n % 5 === 0 ? 1 : 0, file_path: '/library/迷宫饭/Season 1/placeholder.mkv' }
            : { state: 'missing', quality_score: null, upgraded_count: 0, file_path: null },
        ),
      },
    ],
  },
  {
    id: 6,
    title_cn: '剧场版 声之形',
    title_jp: '聲の形',
    title_romaji: 'Koe no Katachi',
    media_type: 'movie',
    status: 'active',
    bangumi_id: '184017',
    tmdb_id: '395020',
    fansub_pref: null,
    quality_pref: '1080p',
    seasons: [],
  },
]

// ---- pending ----

export const mockPending: PendingItemDto[] = [
  {
    id: 101,
    raw_name: '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv',
    stage: 'parse',
    reason: '标题形状与记忆不符,置信度 low',
    status: 'pending',
    created_at: isoDaysAgo(0, 9),
    resolved_at: null,
    parsed: {
      title: { value: 'Spy x Family', source: 'name', confidence: 'low' },
      season: { value: 2, source: 'name', confidence: 'high' },
      episode: { value: 6, source: 'name', confidence: 'high' },
      fansub: { value: 'YoyoSubs', source: 'name', confidence: 'medium' },
      resolution: { value: '1080p', source: 'name', confidence: 'high' },
    },
    candidates: [
      { series_id: 4, title: '间谍过家家', score: 0.71 },
      { series_id: 12, title: '间谍教室', score: 0.43 },
    ],
  },
  {
    id: 102,
    raw_name: 'Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]',
    stage: 'parse',
    reason: '季号缺失,需人工确认',
    status: 'pending',
    created_at: isoDaysAgo(0, 8),
    resolved_at: null,
    parsed: {
      title: { value: '药屋少女的呢喃', source: 'memory', confidence: 'medium' },
      season: { value: 2, source: 'folder', confidence: 'low' },
      episode: { value: 17, source: 'name', confidence: 'high' },
      fansub: { value: 'Kamigakari', source: 'memory', confidence: 'high' },
      resolution: { value: '1080p', source: 'name', confidence: 'high' },
    },
    candidates: [{ series_id: 2, title: '药屋少女的呢喃', score: 0.88 }],
  },
  {
    id: 103,
    raw_name: 'Sousou no Frieren S1 - 12v2 (B-Global 1920x1080 WebRip AAC).mkv',
    stage: 'parse',
    reason: 'V2 版本与已归档文件冲突',
    status: 'pending',
    created_at: isoDaysAgo(1, 21),
    resolved_at: null,
    parsed: {
      title: { value: '葬送的芙莉莲', source: 'memory', confidence: 'medium' },
      season: { value: 1, source: 'name', confidence: 'medium' },
      episode: { value: 12, source: 'name', confidence: 'medium' },
      fansub: { value: 'B-Global', source: 'name', confidence: 'low' },
      resolution: { value: '1080p', source: 'name', confidence: 'high' },
    },
    candidates: [{ series_id: 1, title: '葬送的芙莉莲', score: 0.92 }],
  },
  {
    id: 104,
    raw_name: 'Sousou no Frieren S1 - 24 (B-Global 1920x1080 WebRip AAC).mkv',
    stage: 'parse',
    reason: 'LLM 判定标题可信,等待确认',
    status: 'pending',
    created_at: isoDaysAgo(1, 19),
    resolved_at: null,
    parsed: {
      title: { value: '葬送的芙莉莲', source: 'llm', confidence: 'medium' },
      season: { value: 1, source: 'folder', confidence: 'low' },
      episode: { value: 24, source: 'name', confidence: 'high' },
      fansub: { value: 'B-Global', source: 'name', confidence: 'low' },
      resolution: { value: '1080p', source: 'name', confidence: 'medium' },
    },
    candidates: [{ series_id: 1, title: '葬送的芙莉莲', score: 0.9 }],
  },
]

// ---- audit ----

export const mockAudit: AuditDto[] = [
  {
    id: 3,
    operation_id: 'op-20260905-0003',
    entity: 'episode',
    entity_id: 201,
    action: 'organize.rollback',
    instruction: { from: '/library/葬送的芙莉莲/Season 2/E20.mkv' },
    reverse: {},
    actor: 'manual',
    created_at: isoDaysAgo(0, 10),
  },
  {
    id: 2,
    operation_id: 'op-20260905-0002',
    entity: 'episode',
    entity_id: 202,
    action: 'organize.rename',
    instruction: {
      src: '/downloads/[SubGroup] Dungeon Meshi - 20 [1080p].mkv',
      dst: '/library/迷宫饭/Season 1/迷宫饭 - S01E20.1080p.mkv',
    },
    reverse: {
      src: '/library/迷宫饭/Season 1/迷宫饭 - S01E20.1080p.mkv',
      dst: '/downloads/[SubGroup] Dungeon Meshi - 20 [1080p].mkv',
    },
    actor: 'auto',
    created_at: isoDaysAgo(0, 8),
  },
  {
    id: 1,
    operation_id: 'op-20260905-0001',
    entity: 'episode',
    entity_id: 203,
    action: 'organize.upgrade',
    instruction: {
      old: '/library/迷宫饭/Season 1/迷宫饭 - S01E15.720p.mkv',
      new: '/library/迷宫饭/Season 1/迷宫饭 - S01E15.1080p.mkv',
      score_old: 8,
      score_new: 11,
    },
    reverse: {
      src: '/library/迷宫饭/Season 1/迷宫饭 - S01E15.1080p.mkv',
      dst: '/library/迷宫饭/Season 1/迷宫饭 - S01E15.720p.mkv',
    },
    actor: 'auto',
    created_at: isoDaysAgo(0, 7),
  },
]

// ---- subscriptions ----

export const mockSubscriptions: SubscriptionDto[] = [
  {
    id: 1,
    series_id: 2,
    title: '药屋少女的呢喃',
    season_id: 2,
    season_number: 2,
    state: 'airing',
    fansub_pref: 'Kamigakari',
    episodes_total: 24,
    episodes_aired: 16,
    episodes_collected: 15,
    next_air_date: dateDaysAgo(-2),
    reduced_frequency: false,
    enabled: true,
  },
  {
    id: 2,
    series_id: 5,
    title: '迷宫饭',
    season_id: 6,
    season_number: 1,
    state: 'airing',
    fansub_pref: 'NC-Raws',
    episodes_total: 24,
    episodes_aired: 20,
    episodes_collected: 20,
    next_air_date: dateDaysAgo(-1),
    reduced_frequency: false,
    enabled: true,
  },
  {
    id: 3,
    series_id: 3,
    title: '我推的孩子',
    season_id: 4,
    season_number: 2,
    state: 'airing',
    fansub_pref: null,
    episodes_total: 13,
    episodes_aired: 4,
    episodes_collected: 4,
    next_air_date: dateDaysAgo(-4),
    reduced_frequency: false,
    enabled: true,
  },
  {
    id: 4,
    series_id: 1,
    title: '葬送的芙莉莲',
    season_id: 1,
    season_number: 1,
    state: 'collected',
    fansub_pref: '栀次元字幕组',
    episodes_total: 28,
    episodes_aired: 28,
    episodes_collected: 28,
    next_air_date: null,
    reduced_frequency: true,
    enabled: true,
  },
]

// ---- rss sources ----

export const mockRssSources: RssSourceDto[] = [
  {
    id: 1,
    url: 'https://mikanani.me/RSS/MyBangumi?token=***',
    has_token: true,
    season_id: 2,
    enabled: true,
    last_polled_at: isoDaysAgo(0, 7),
  },
  {
    id: 2,
    url: 'https://mikanime.tv/RSS/Bangumi?bangumiId=382241&subgroupid=583',
    has_token: false,
    season_id: 6,
    enabled: true,
    last_polled_at: isoDaysAgo(0, 6),
  },
  {
    id: 3,
    url: 'https://bangumi.moe/rss/moe/6556',
    has_token: false,
    season_id: 4,
    enabled: false,
    last_polled_at: isoDaysAgo(3, 2),
  },
]

// ---- settings ----

export const mockSettings: SettingsDto = {
  downloader: {
    client: 'qbittorrent',
    url: 'http://127.0.0.1:8080',
  },
  llm: {
    enabled: false,
    model: 'deepseek-chat',
  },
  autonomy: 'medium',
  quality: {
    upgrade_threshold: 2,
    max_upgrades_per_episode: 2,
    copy_policy: 'copy',
    skip_size_gb: 20,
  },
  naming: {
    template: '{title_cn}/Season {SS}/{title_cn} - S{SS}E{EE}.{quality}.mkv',
    title_language: 'title_cn',
  },
}

// ---- metrics ----

export const mockMetrics: Metrics = {
  manual_intervention_rate: 0.048,
  weekly_archived: 26,
  llm_call_rate: 0.071,
  pending_count: mockPending.length,
  levels: {
    total: 431,
    l1_high: 291,
    l2_hit: 96,
    l3_entered: 44,
    llm_calls: 31,
  },
  weekly_curve: Array.from({ length: 7 }, (_, i) => ({
    date: dateDaysAgo(6 - i),
    archived: [2, 5, 3, 7, 4, 2, 3][i] ?? 0,
    llm_calls: [0, 1, 0, 2, 0, 0, 1][i] ?? 0,
    pending: [1, 0, 2, 1, 3, 1, 2][i] ?? 0,
  })),
}
