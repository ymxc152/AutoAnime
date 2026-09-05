/*
 * Mock fixtures —— 形状与 src/api/types.ts(已对齐 E2 后端 schema)严格一致,
 * 兼作契约类型的"可运行文档"。E2 合并对齐后:mock 仅作后端未启动时的演示。
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
      file_hash: plan.file_path === null ? null : `hash-${seasonId}-${n}`,
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
        status: 'collected',
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
        status: 'airing',
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
        status: 'ended',
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
        status: 'airing',
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
        status: 'collected',
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
        status: 'airing',
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

// ---- pending(对齐 PendingOut:草稿在 context,resolution 读侧已解析) ----

export const mockPending: PendingItemDto[] = [
  {
    id: 101,
    raw_name: '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv',
    context: {
      title: 'Spy x Family',
      season: 2,
      episode: 6,
      segment: 'episode',
      fansub: 'YoyoSubs',
      folder: 'Spy x Family/Season 2',
    },
    stage: 'parse',
    reason: '标题形状与记忆不符,置信度 low',
    status: 'pending',
    resolution: null,
    resolved_by: null,
    created_at: isoDaysAgo(0, 9),
    resolved_at: null,
  },
  {
    id: 102,
    raw_name: 'Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]',
    context: {
      title: '药屋少女的呢喃',
      season: 2,
      episode: 17,
      segment: 'episode',
      fansub: 'Kamigakari',
      parent_path: '/downloads/Kusuriya',
    },
    stage: 'parse',
    reason: '季号缺失,需人工确认',
    status: 'pending',
    resolution: null,
    resolved_by: null,
    created_at: isoDaysAgo(0, 8),
    resolved_at: null,
  },
  {
    id: 103,
    raw_name: 'Sousou no Frieren S1 - 12v2 (B-Global 1920x1080 WebRip AAC).mkv',
    context: {
      title: '葬送的芙莉莲',
      season: 1,
      episode: 12,
      segment: 'episode',
      fansub: 'B-Global',
    },
    stage: 'organize',
    reason: 'V2 版本与已归档文件冲突',
    status: 'pending',
    resolution: null,
    resolved_by: null,
    created_at: isoDaysAgo(1, 21),
    resolved_at: null,
  },
  {
    id: 104,
    raw_name: 'Sousou no Frieren S1 - 24 (B-Global 1920x1080 WebRip AAC).mkv',
    context: {
      title: '葬送的芙莉莲',
      season: 1,
      episode: 24,
      segment: 'episode',
      fansub: 'B-Global',
    },
    stage: 'parse',
    reason: 'LLM 判定标题可信,等待确认',
    status: 'pending',
    resolution: null,
    resolved_by: null,
    created_at: isoDaysAgo(1, 19),
    resolved_at: null,
  },
]

// ---- audit(对齐 AuditOut:无 created_at,组内序按 id) ----

export const mockAudit: AuditDto[] = [
  {
    id: 3,
    operation_id: 'op-20260905-0003',
    entity: 'parse_memory',
    entity_id: 7,
    action: 'demote_pending',
    instruction: { status: 'pending' },
    reverse: { status: 'active' },
    actor: 'auto',
  },
  {
    id: 2,
    operation_id: 'op-20260905-0002',
    entity: 'parse_memory',
    entity_id: 8,
    action: 'memory_hit',
    instruction: { raw_name: 'Kusuriya no Hitorigoto - 17 [V2][1080p][Kamigakari]' },
    reverse: {},
    actor: 'auto',
  },
  {
    id: 1,
    operation_id: 'op-20260905-0001',
    entity: 'pending_queue',
    entity_id: 101,
    action: 'pending_confirm',
    instruction: {
      raw_name: '[YoyoSubs] Spy x Family S02E06 [1080p][CHS].mkv',
      confirmed: { title: 'Spy x Family', season: 2, episode: 6, segment: 'episode', fansub: 'YoyoSubs' },
    },
    reverse: {},
    actor: 'manual',
  },
]

// ---- subscriptions(对齐 SubscriptionOut:series 载体 + 每季进度) ----

export const mockSubscriptions: SubscriptionDto[] = [
  {
    id: 2,
    title_cn: '药屋少女的呢喃',
    title_jp: '薬屋のひとりごと',
    title_romaji: 'Kusuriya no Hitorigoto',
    media_type: 'tv',
    status: 'active',
    fansub_pref: 'Kamigakari',
    quality_pref: '1080p',
    seasons: [
      {
        season_id: 2,
        number: 2,
        status: 'airing',
        episodes_total: 24,
        episodes_missing: 8,
        episodes_organized: 15,
        rss_sources: 1,
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
    fansub_pref: 'NC-Raws',
    quality_pref: '1080p',
    seasons: [
      {
        season_id: 6,
        number: 1,
        status: 'airing',
        episodes_total: 24,
        episodes_missing: 4,
        episodes_organized: 20,
        rss_sources: 1,
      },
    ],
  },
  {
    id: 1,
    title_cn: '葬送的芙莉莲',
    title_jp: '葬送のフリーレン',
    title_romaji: 'Sousou no Frieren',
    media_type: 'tv',
    status: 'active',
    fansub_pref: '栀次元字幕组',
    quality_pref: '1080p',
    seasons: [
      {
        season_id: 1,
        number: 1,
        status: 'collected',
        episodes_total: 28,
        episodes_missing: 0,
        episodes_organized: 28,
        rss_sources: 0,
      },
    ],
  },
]

// ---- rss sources(season_id 非空:外键指向 season.id) ----

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

// ---- settings(对齐 SettingsOut:扁平结构,密钥只回 has_*) ----

export const mockSettings: SettingsDto = {
  dry_run: false,
  l2_enabled: true,
  llm_enabled: false,
  llm_model: 'deepseek-chat',
  reference_enabled: true,
  reference_order: ['bangumi', 'tmdb'],
  library_path: '/library',
  download_path: '/downloads',
  api_host: '127.0.0.1',
  api_port: 8000,
  api_cors_dev_origins: ['http://localhost:5173'],
  api_sse_heartbeat_s: 30,
  api_sse_replay_limit: 200,
  has_api_token: false,
  has_llm_api_key: true,
}

// ---- metrics(对齐 MetricsOut) ----

export const mockMetrics: Metrics = {
  intervention_rate: 0.048,
  audit_total: 62,
  audit_manual: 3,
  by_level: [
    { level: 1, total: 291, llm_called: 0, outcomes: { l1_high: 291 } },
    { level: 2, total: 96, llm_called: 0, outcomes: { memory_hit: 96 } },
    { level: 3, total: 44, llm_called: 31, outcomes: { l3_result: 44 } },
  ],
  llm_call_curve_weekly: Array.from({ length: 8 }, (_, i) => {
    const total = [52, 61, 47, 70, 66, 58, 39, 38][i] ?? 0
    const llmCalled = [4, 7, 3, 9, 6, 2, 0, 0][i] ?? 0
    return {
      bucket: dateDaysAgo((7 - i) * 7).slice(0, 4) + '-W' + String(i + 29).padStart(2, '0'),
      total,
      llm_called: llmCalled,
      llm_rate: total > 0 ? llmCalled / total : null,
    }
  }),
  pending_trend_daily: Array.from({ length: 28 }, (_, i) => ({
    bucket: dateDaysAgo(27 - i),
    created: i % 7 === 3 ? 2 : 0,
    resolved: i % 7 === 4 ? 1 : 0,
  })),
  pending_open: mockPending.length,
  episode_states: { missing: 30, downloading: 1, organized: 87, upgraded: 4 },
  memory_sources: [
    { source: 'manual', status: 'active', rows: 12 },
    { source: 'llm_confirmed', status: 'active', rows: 5 },
    { source: 'llm_auto', status: 'pending', rows: 3 },
  ],
}
