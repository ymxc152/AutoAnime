"""
autoanime Bangumi/BGM 查询

对应原 `AutoAnimeMv.py::Auxiliary_QueryBangumiChineseTitle`。
原代码中 "Bgm" 和 "Bangumi" 两个开关控制同一 bgm.tv 数据源；
本模块暴露 `Auxiliary_QueryBangumiChineseTitle`，用于剧名链 & 回退链路。
"""

from urllib.parse import quote

from .. import state
from ..logging_utils import Auxiliary_Log
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)
from .http import Auxiliary_Http


def Auxiliary_QueryBangumiChineseTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''仅通过 Bangumi 查询中文标题；未命中中文时返回 None'''
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
    if QueryName in [None, ''] or state.USEBANGUMIAPI != True:
        return None

    CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(QueryName, CandidateEn, CandidateRomaji)
    if CanonicalZh not in [None, '']:
        return CanonicalZh

    CandidateKeys = Auxiliary_GetStandardTitleCacheCandidates(QueryName)
    if QueryName not in CandidateKeys:
        CandidateKeys.insert(0, QueryName)
    for CacheKey in CandidateKeys:
        CacheValue = None
        if type(state.BangumiAPIDataCache) == dict and CacheKey in state.BangumiAPIDataCache:
            CacheValue = state.BangumiAPIDataCache.get(CacheKey)
            Auxiliary_Log(f'{CacheValue} << Bangumi内存缓存查询结果')
        else:
            CacheValue = Auxiliary_GetPersistentCache('Bangumi', CacheKey)
            if CacheValue not in [None, '']:
                if type(state.BangumiAPIDataCache) != dict:
                    state.BangumiAPIDataCache = {}
                state.BangumiAPIDataCache[CacheKey] = CacheValue
                Auxiliary_Log(f'{CacheValue} << Bangumi持久化缓存查询结果')
        CacheValue = Auxiliary_NormalizeApiTitle(CacheValue)
        if CacheValue in [None, ''] or Auxiliary_HasChineseText(CacheValue) == False:
            continue
        return CacheValue

    BangumiApiData = Auxiliary_Http(
        f"https://api.bgm.tv/search/subject/{quote(QueryName)}?type=2&responseGroup=medium&max_results=1",
        ResponseType='json',
        Timeout=20,
    )
    if type(BangumiApiData) != dict:
        Auxiliary_Log(f'BangumiApi查询失败: {QueryName}', 'WARNING')
        return None
    ResultList = BangumiApiData.get('list', [])
    if type(ResultList) != list or ResultList == [] or type(ResultList[0]) != dict:
        Auxiliary_Log(f'BangumiApi没有检索到关于 {QueryName} 内容', 'WARNING')
        return None

    AnimeData = ResultList[0]
    ApiTitle = Auxiliary_NormalizeApiTitle(AnimeData.get('name_cn') or AnimeData.get('name') or '')
    if ApiTitle in [None, ''] or Auxiliary_HasChineseText(ApiTitle) == False:
        Auxiliary_Log(f'BangumiApi未返回可用中文标题: {QueryName}', 'WARNING')
        return None

    CandidateEnForUpsert = CandidateEn
    if CandidateEnForUpsert in [None, ''] and Auxiliary_HasChineseText(QueryName) == False:
        CandidateEnForUpsert = QueryName
    CandidateAliases = [QueryName, CandidateEn, CandidateRomaji]
    if type(AliasList) == list:
        CandidateAliases.extend(AliasList)
    CandidateAliases = [Auxiliary_NormalizeDisplayTitle(Item) for Item in CandidateAliases if Item not in [None, '']]

    _, CanonicalTitle = Auxiliary_UpsertCanonicalTitle(
        ApiTitle, CandidateEnForUpsert, CandidateRomaji, 'Bangumi', CandidateAliases,
    )
    if CanonicalTitle not in [None, ''] and Auxiliary_HasChineseText(CanonicalTitle):
        ApiTitle = CanonicalTitle
    for CacheKey in CandidateKeys:
        state.BangumiAPIDataCache[CacheKey] = ApiTitle
        Auxiliary_SetPersistentCache('Bangumi', CacheKey, ApiTitle)
    Auxiliary_Log(f'{ApiTitle} << BangumiApi查询结果')
    return ApiTitle
