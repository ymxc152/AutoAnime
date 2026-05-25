"""
autoanime TMDB Api 查询

对应原 `AutoAnimeMv.py`:
- `Auxiliary_QueryTMDBChineseTitle`
- `Auxiliary_QueryTMDBEnglishTitle`
- `Auxiliary_ParseTMDBTvDetailsSeasonLayout`
- `Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout`
- `Auxiliary_GetTMDBTvSeasonLayoutBySeriesId`
- `Auxiliary_ResolveTMDBTvSeriesIdFromEnglishQuery`
- `Auxiliary_ResolveTMDBTvIdForJujutsuKaisen`
"""

from urllib.parse import quote

from .. import state
from ..config_loader import Auxiliary_GetTMDBBearerToken
from ..logging_utils import Auxiliary_Log
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeAliasKey,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)
from .http import Auxiliary_Http


def Auxiliary_QueryTMDBChineseTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''仅通过 TMDB 查询中文标题；未命中中文时返回 None'''
    from ..cache.canonical import (
        Auxiliary_ResolveCanonicalTitleByAliases,
        Auxiliary_UpsertCanonicalTitle,
    )
    from ..cache.persistent import (
        Auxiliary_GetPersistentCache,
        Auxiliary_SetPersistentCache,
    )
    from ..identification.title_chain import Auxiliary_GetStandardTitleCacheCandidates

    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    CandidateEn = Auxiliary_NormalizeDisplayTitle(CandidateEn)
    CandidateRomaji = Auxiliary_NormalizeDisplayTitle(CandidateRomaji)
    if QueryName in [None, ''] or state.USETMDBAPI != True:
        return None

    CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(QueryName, CandidateEn, CandidateRomaji)
    if CanonicalZh not in [None, '']:
        return CanonicalZh
    if Auxiliary_GetTMDBBearerToken() in [None, '']:
        Auxiliary_Log('TMDBApi 已启用但未配置 token，跳过 TMDB 查询', 'WARNING')
        return None

    CandidateKeys = Auxiliary_GetStandardTitleCacheCandidates(QueryName)
    if QueryName not in CandidateKeys:
        CandidateKeys.insert(0, QueryName)
    for CacheKey in CandidateKeys:
        CacheValue = None
        if type(state.TMDBAPIDataCache) == dict and CacheKey in state.TMDBAPIDataCache:
            CacheValue = state.TMDBAPIDataCache.get(CacheKey)
            Auxiliary_Log(f'{CacheValue} << TMDB内存缓存查询结果')
        else:
            CacheValue = Auxiliary_GetPersistentCache('TMDB', CacheKey)
            if CacheValue not in [None, '']:
                if type(state.TMDBAPIDataCache) != dict:
                    state.TMDBAPIDataCache = {}
                state.TMDBAPIDataCache[CacheKey] = CacheValue
                Auxiliary_Log(f'{CacheValue} << TMDB持久化缓存查询结果')
        CacheValue = Auxiliary_NormalizeApiTitle(CacheValue)
        if CacheValue in [None, ''] or Auxiliary_HasChineseText(CacheValue) == False:
            continue
        return CacheValue

    TMDBApiData = Auxiliary_Http(
        f'https://api.themoviedb.org/3/search/tv?query={quote(QueryName)}&include_adult=true&language=zh&page=1',
        ResponseType='json',
        Timeout=20,
    )
    if type(TMDBApiData) != dict:
        Auxiliary_Log(f'TMDBApi返回异常: {QueryName}', 'WARNING')
        return None
    ResultList = TMDBApiData.get('results', [])
    if type(ResultList) != list or ResultList == []:
        Auxiliary_Log(f'TMDBApi没有检索到关于 {QueryName} 内容', 'WARNING')
        return None

    ApiTitle = ''
    for ResultItem in ResultList:
        if type(ResultItem) != dict:
            continue
        CandidateTitle = Auxiliary_NormalizeApiTitle(ResultItem.get('name') or ResultItem.get('original_name') or '')
        if CandidateTitle not in [None, ''] and Auxiliary_HasChineseText(CandidateTitle):
            ApiTitle = CandidateTitle
            break
    if ApiTitle in [None, '']:
        Auxiliary_Log(f'TMDBApi命中结果但未返回中文标题: {QueryName}', 'WARNING')
        return None

    CandidateEnForUpsert = CandidateEn
    if CandidateEnForUpsert in [None, ''] and Auxiliary_HasChineseText(QueryName) == False:
        CandidateEnForUpsert = QueryName
    CandidateAliases = [QueryName, CandidateEn, CandidateRomaji]
    if type(AliasList) == list:
        CandidateAliases.extend(AliasList)
    CandidateAliases = [Auxiliary_NormalizeDisplayTitle(Item) for Item in CandidateAliases if Item not in [None, '']]

    _, CanonicalTitle = Auxiliary_UpsertCanonicalTitle(
        ApiTitle, CandidateEnForUpsert, CandidateRomaji, 'TMDB', CandidateAliases,
    )
    if CanonicalTitle not in [None, ''] and Auxiliary_HasChineseText(CanonicalTitle):
        ApiTitle = CanonicalTitle
    for CacheKey in CandidateKeys:
        state.TMDBAPIDataCache[CacheKey] = ApiTitle
        Auxiliary_SetPersistentCache('TMDB', CacheKey, ApiTitle)
    Auxiliary_Log(f'{ApiTitle} << TMDBApi查询结果')
    return ApiTitle


def Auxiliary_QueryTMDBEnglishTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''TMDB en-US 检索，返回英文剧名（不要求中文）'''
    from ..cache.canonical import Auxiliary_UpsertCanonicalTitle
    from ..cache.persistent import Auxiliary_SetPersistentCache
    from ..identification.title_chain import Auxiliary_GetStandardTitleCacheCandidates

    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    CandidateEn = Auxiliary_NormalizeDisplayTitle(CandidateEn)
    CandidateRomaji = Auxiliary_NormalizeDisplayTitle(CandidateRomaji)
    if QueryName in [None, ''] or state.USETMDBAPI != True:
        return None
    if Auxiliary_GetTMDBBearerToken() in [None, '']:
        Auxiliary_Log('TMDBApi 已启用但未配置 token，跳过 TMDB 英文查询', 'WARNING')
        return None
    CandidateKeys = Auxiliary_GetStandardTitleCacheCandidates(QueryName)
    if QueryName not in CandidateKeys:
        CandidateKeys.insert(0, QueryName)
    for CacheKey in CandidateKeys:
        RawVal = None
        if type(state.TMDBAPIDataCache) == dict and f'en:{CacheKey}' in state.TMDBAPIDataCache:
            RawVal = state.TMDBAPIDataCache.get(f'en:{CacheKey}')
        else:
            Group = state.PersistentApiCache.get('TMDB_EN', {}) if type(state.PersistentApiCache) == dict else {}
            Rec = Group.get(CacheKey) if type(Group) == dict else None
            if type(Rec) == dict and Rec.get('value') not in [None, '']:
                RawVal = Rec.get('value')
        if RawVal not in [None, '']:
            return Auxiliary_NormalizeDisplayTitle(str(RawVal))
    TMDBApiData = Auxiliary_Http(
        f'https://api.themoviedb.org/3/search/tv?query={quote(QueryName)}&include_adult=true&language=en-US&page=1',
        ResponseType='json',
        Timeout=20,
    )
    if type(TMDBApiData) != dict:
        Auxiliary_Log(f'TMDBApi(EN)返回异常: {QueryName}', 'WARNING')
        return None
    ResultList = TMDBApiData.get('results', [])
    if type(ResultList) != list or ResultList == []:
        Auxiliary_Log(f'TMDBApi(EN)没有检索到关于 {QueryName} 内容', 'WARNING')
        return None
    ApiTitle = ''
    for ResultItem in ResultList:
        if type(ResultItem) != dict:
            continue
        ApiTitle = Auxiliary_NormalizeDisplayTitle(ResultItem.get('name') or ResultItem.get('original_name') or '')
        if ApiTitle not in [None, '']:
            break
    if ApiTitle in [None, '']:
        return None
    CandidateAliases = [QueryName, CandidateEn, CandidateRomaji]
    if type(AliasList) == list:
        CandidateAliases.extend(AliasList)
    CandidateAliases = [Auxiliary_NormalizeDisplayTitle(Item) for Item in CandidateAliases if Item not in [None, '']]
    EnForUpsert = CandidateEn if CandidateEn not in [None, ''] else ApiTitle
    Auxiliary_UpsertCanonicalTitle(
        '', EnForUpsert if EnForUpsert not in [None, ''] else ApiTitle, CandidateRomaji, 'TMDB', CandidateAliases + [ApiTitle],
    )
    if type(state.TMDBAPIDataCache) != dict:
        state.TMDBAPIDataCache = {}
    for CacheKey in CandidateKeys:
        state.TMDBAPIDataCache[f'en:{CacheKey}'] = ApiTitle
        Auxiliary_SetPersistentCache('TMDB_EN', CacheKey, ApiTitle)
    Auxiliary_Log(f'{ApiTitle} << TMDBApi(EN)查询结果')
    return ApiTitle


def Auxiliary_ParseTMDBTvDetailsSeasonLayout(DetailsData):
    '''从 TMDB tv/{id} 详情中解析正片分季集数列表，忽略第 0 季特典。'''
    if type(DetailsData) != dict:
        return []
    RawSeasons = DetailsData.get('seasons', [])
    if type(RawSeasons) != list:
        return []
    Pairs = []
    for Item in RawSeasons:
        if type(Item) != dict:
            continue
        try:
            Sn = int(Item.get('season_number', -1))
            Ec = int(Item.get('episode_count', 0))
        except (TypeError, ValueError):
            continue
        if Sn < 1 or Ec < 1:
            continue
        Pairs.append((Sn, Ec))
    Pairs.sort(key=lambda X: X[0])
    return Pairs


def Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(AbsEp, SeasonPairs):
    '''
    将「全剧累计集号」映射到 (季号, 该季内的集号)。
    最后一季若累计集超出 TMDB 已登记的 episode_count，仍归入最后一季并顺延集号。
    '''
    if AbsEp < 1 or type(SeasonPairs) != list or SeasonPairs == []:
        return None
    Prefix = 0
    LastIndex = len(SeasonPairs) - 1
    for Idx, (SeasonNum, EpCount) in enumerate(SeasonPairs):
        if Idx == LastIndex:
            return SeasonNum, AbsEp - Prefix
        if AbsEp <= Prefix + EpCount:
            return SeasonNum, AbsEp - Prefix
        Prefix += EpCount
    return None


def Auxiliary_GetTMDBTvSeasonLayoutBySeriesId(tv_id):
    from ..cache.persistent import Auxiliary_GetPersistentCache, Auxiliary_SetPersistentCache

    try:
        TvIdInt = int(tv_id)
    except (TypeError, ValueError):
        return []
    if TvIdInt in state.TMDBTvSeasonLayoutMemoryCache:
        return state.TMDBTvSeasonLayoutMemoryCache[TvIdInt]
    CachedRaw = Auxiliary_GetPersistentCache('TMDBTvSeasons', f'id:{TvIdInt}')
    if type(CachedRaw) == list and CachedRaw != []:
        Pairs = []
        for Row in CachedRaw:
            if type(Row) in (list, tuple) and len(Row) >= 2:
                try:
                    Pairs.append((int(Row[0]), int(Row[1])))
                except (TypeError, ValueError):
                    continue
        if Pairs != []:
            state.TMDBTvSeasonLayoutMemoryCache[TvIdInt] = Pairs
            return Pairs
    if state.USETMDBAPI != True or Auxiliary_GetTMDBBearerToken() in [None, '']:
        return []
    Details = Auxiliary_Http(
        f'https://api.themoviedb.org/3/tv/{TvIdInt}',
        ResponseType='json',
        Timeout=25,
    )
    Pairs = Auxiliary_ParseTMDBTvDetailsSeasonLayout(Details)
    if Pairs != []:
        state.TMDBTvSeasonLayoutMemoryCache[TvIdInt] = Pairs
        Auxiliary_SetPersistentCache(
            'TMDBTvSeasons',
            f'id:{TvIdInt}',
            [[Sn, Ec] for Sn, Ec in Pairs],
        )
    return Pairs


def Auxiliary_ResolveTMDBTvSeriesIdFromEnglishQuery(QueryName):
    from ..cache.persistent import Auxiliary_GetPersistentCache, Auxiliary_SetPersistentCache

    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    if QueryName in [None, '']:
        return None
    AliasKey = Auxiliary_NormalizeAliasKey(QueryName)
    if AliasKey in [None, '']:
        return None
    if AliasKey in state.TMDBTvSeriesIdMemoryCache:
        return state.TMDBTvSeriesIdMemoryCache[AliasKey]
    CachedId = Auxiliary_GetPersistentCache('TMDBTvSeriesId', AliasKey)
    try:
        CachedId = int(CachedId)
    except (TypeError, ValueError):
        CachedId = 0
    if CachedId > 0:
        state.TMDBTvSeriesIdMemoryCache[AliasKey] = CachedId
        return CachedId
    if state.USETMDBAPI != True or Auxiliary_GetTMDBBearerToken() in [None, '']:
        return None
    SearchData = Auxiliary_Http(
        f'https://api.themoviedb.org/3/search/tv?query={quote(QueryName)}&include_adult=false&language=en-US&page=1',
        ResponseType='json',
        Timeout=20,
    )
    if type(SearchData) != dict:
        return None
    ResultList = SearchData.get('results', [])
    if type(ResultList) != list or ResultList == [] or type(ResultList[0]) != dict:
        return None
    Tid = ResultList[0].get('id')
    try:
        Tid = int(Tid)
    except (TypeError, ValueError):
        return None
    if Tid < 1:
        return None
    state.TMDBTvSeriesIdMemoryCache[AliasKey] = Tid
    Auxiliary_SetPersistentCache('TMDBTvSeriesId', AliasKey, Tid)
    return Tid


def Auxiliary_ResolveTMDBTvIdForJujutsuKaisen(NameEN, NameRomaji):
    QueryList = []
    for Q in (NameEN, NameRomaji, 'Jujutsu Kaisen'):
        Qn = Auxiliary_NormalizeDisplayTitle(Q or '')
        if Qn != '' and Qn not in QueryList:
            QueryList.append(Qn)
    for Qn in QueryList:
        Tid = Auxiliary_ResolveTMDBTvSeriesIdFromEnglishQuery(Qn)
        if Tid not in [None, ''] and int(Tid) > 0:
            return int(Tid)
    return None
