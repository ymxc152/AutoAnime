"""
autoanime 剧名标准化链

对应原 `AutoAnimeMv.py`:
- `Auxiliary_ResolvePlannedTitleChain`
- `Auxiliary_GetStandardTitleCacheCandidates`
- `Auxiliary_GetStandardTitleFromCache`
- `Auxiliary_ApplyStandardTitleCacheToFileInfoRecord`
- `Auxiliary_ShouldCacheResolvedFileInfo`
"""

from os import path
from re import sub

from .. import state
from ..logging_utils import Auxiliary_Exit
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)


def Auxiliary_GetStandardTitleCacheCandidates(QueryName):
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    if QueryName == '':
        return []

    CandidateList = []

    def AddCandidate(Value):
        Value = Auxiliary_NormalizeDisplayTitle(Value)
        if Value not in [None, ''] and Value not in CandidateList:
            CandidateList.append(Value)

    CompactName = sub(r'\s+', ' ', QueryName).strip()
    AddCandidate(QueryName)
    AddCandidate(CompactName)
    AddCandidate(CompactName.replace(' ', '-'))
    AddCandidate(CompactName.replace('-', ' '))
    AddCandidate(CompactName.replace(' ', ''))
    AddCandidate(CompactName.replace('-', ''))
    return CandidateList


def Auxiliary_GetStandardTitleFromCache(QueryName):
    from ..cache.canonical import (
        Auxiliary_ResolveCanonicalTitleByAliases,
        Auxiliary_UpsertCanonicalTitle,
    )
    from ..cache.persistent import Auxiliary_GetPersistentCache

    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    if QueryName == '':
        return None
    CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(QueryName)
    if CanonicalZh not in [None, '']:
        return CanonicalZh
    CacheGroupList = [
        ('Bangumi', state.BangumiAPIDataCache),
        ('TMDB', state.TMDBAPIDataCache),
    ]
    for CacheKey in Auxiliary_GetStandardTitleCacheCandidates(QueryName):
        for CacheGroup, InMemoryCache in CacheGroupList:
            if type(InMemoryCache) == dict and CacheKey in InMemoryCache:
                CacheValue = InMemoryCache[CacheKey]
            else:
                CacheValue = Auxiliary_GetPersistentCache(CacheGroup, CacheKey)
                if CacheValue not in [None, ''] and type(InMemoryCache) == dict:
                    InMemoryCache[CacheKey] = CacheValue
            CacheValue = Auxiliary_NormalizeDisplayTitle(CacheValue)
            if CacheValue in [None, '']:
                continue
            CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(QueryName, CacheValue, CacheKey)
            if CanonicalZh not in [None, '']:
                return CanonicalZh
            CandidateZh = Auxiliary_NormalizeApiTitle(CacheValue)
            CandidateEn = CacheKey if Auxiliary_HasChineseText(CacheKey) == False else ''
            if Auxiliary_HasChineseText(CandidateZh) == False:
                CandidateZh = ''
                if CandidateEn in [None, '']:
                    CandidateEn = CacheValue
            _, CanonicalZh = Auxiliary_UpsertCanonicalTitle(
                CandidateZh, CandidateEn, '', CacheGroup, [QueryName, CacheKey, CacheValue],
            )
            if CanonicalZh not in [None, '']:
                return CanonicalZh
            if CandidateZh not in [None, '']:
                return CandidateZh
    return None


def Auxiliary_ResolvePlannedTitleChain(AINameZH, NameEN, NameRomaji, QueryFileName):
    '''
    剧名：TMDB 中文 → Bangumi 中文 → TMDB 英文 → OpenAI 译中文。
    返回 (中文主名, CanonicalID, NameEN, NameRomaji)；失败则 Auxiliary_Exit。
    '''
    from ..apis.bangumi import Auxiliary_QueryBangumiChineseTitle
    from ..apis.openai_client import Auxiliary_OpenAITranslateForeignTitleToChinese
    from ..apis.tmdb import (
        Auxiliary_QueryTMDBChineseTitle,
        Auxiliary_QueryTMDBEnglishTitle,
    )
    from ..cache.canonical import Auxiliary_UpsertCanonicalTitle
    from ..cache.manual_whitelist import Auxiliary_GetManualWhitelistedTitle

    AINameZH = Auxiliary_NormalizeApiTitle(AINameZH or '')
    NameEN = Auxiliary_NormalizeDisplayTitle(NameEN or '')
    NameRomaji = Auxiliary_NormalizeDisplayTitle(NameRomaji or '')
    BaseName = path.basename(str(QueryFileName))
    queries = []
    for q in [AINameZH, NameRomaji, NameEN, BaseName]:
        qn = Auxiliary_NormalizeDisplayTitle(q or '')
        if qn not in [None, ''] and qn not in queries:
            queries.append(qn)
    AliasBundle = queries.copy()

    if (ManualWhitelistedTitle := Auxiliary_GetManualWhitelistedTitle(*queries)) not in [None, '']:
        cid, zh = Auxiliary_UpsertCanonicalTitle(
            ManualWhitelistedTitle, NameEN, NameRomaji, 'manual', AliasBundle,
        )
        return (zh if zh not in [None, ''] else ManualWhitelistedTitle), (cid or ''), NameEN, NameRomaji

    for q in queries:
        if state.USETMDBAPI == True:
            zh = Auxiliary_QueryTMDBChineseTitle(q, CandidateEn=NameEN or q, CandidateRomaji=NameRomaji, AliasList=AliasBundle)
            if zh not in [None, ''] and Auxiliary_HasChineseText(zh):
                cid, final = Auxiliary_UpsertCanonicalTitle(zh, NameEN, NameRomaji, 'TMDB', AliasBundle)
                return (final if final not in [None, ''] else zh), (cid or ''), NameEN, NameRomaji
    for q in queries:
        if state.USEBANGUMIAPI == True:
            zh = Auxiliary_QueryBangumiChineseTitle(q, CandidateEn=NameEN or q, CandidateRomaji=NameRomaji, AliasList=AliasBundle)
            if zh not in [None, ''] and Auxiliary_HasChineseText(zh):
                cid, final = Auxiliary_UpsertCanonicalTitle(zh, NameEN, NameRomaji, 'Bangumi', AliasBundle)
                return (final if final not in [None, ''] else zh), (cid or ''), NameEN, NameRomaji

    EnTitle = None
    for q in queries:
        if state.USETMDBAPI == True:
            EnTitle = Auxiliary_QueryTMDBEnglishTitle(q, CandidateEn=NameEN or q, CandidateRomaji=NameRomaji, AliasList=AliasBundle)
            if EnTitle not in [None, '']:
                if NameEN in [None, '']:
                    NameEN = EnTitle
                break
    ForeignForTranslate = EnTitle or NameEN or NameRomaji or ''
    if ForeignForTranslate in [None, ''] and queries:
        ForeignForTranslate = queries[0]
    if state.USEOPENAIAPI == True:
        Translated = Auxiliary_OpenAITranslateForeignTitleToChinese(ForeignForTranslate)
        if Translated not in [None, '']:
            cid, final = Auxiliary_UpsertCanonicalTitle(Translated, NameEN or ForeignForTranslate, NameRomaji, 'OpenAI', AliasBundle)
            return (final if final not in [None, ''] else Translated), (cid or ''), NameEN, NameRomaji
    if AINameZH not in [None, ''] and Auxiliary_HasChineseText(AINameZH):
        cid, final = Auxiliary_UpsertCanonicalTitle(AINameZH, NameEN, NameRomaji, 'OpenAI', AliasBundle)
        return (final if final not in [None, ''] else AINameZH), (cid or ''), NameEN, NameRomaji
    Auxiliary_Exit('剧名解析链失败：TMDB 中文、Bangumi、TMDB 英文与 OpenAI 译中文均未得到可用简体中文剧名，已中止整理')


def Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(CacheRecord):
    from ..cache.canonical import (
        Auxiliary_ContractJujutsuKaisenChineseTitle,
        Auxiliary_ResolveCanonicalTitleByAliases,
        Auxiliary_UpsertCanonicalTitle,
    )
    from .episode_rules import (
        Auxiliary_NormalizeEpisodeToken,
        Auxiliary_RemappedJujutsuKaisenSeasonEpisode,
    )

    if type(CacheRecord) != dict or all([Key in CacheRecord for Key in ['SE', 'EP', 'RAWSE', 'RAWEP', 'RAWName']]) != True:
        return CacheRecord, False
    FixedRecord = CacheRecord.copy()
    FixedRecord['RAWEP'] = str(FixedRecord.get('RAWEP', ''))
    FixedRecord['RAWEP'], _ = Auxiliary_NormalizeEpisodeToken(FixedRecord['RAWEP'])
    FixedRecord['RAWName'] = Auxiliary_NormalizeApiTitle(FixedRecord.get('RAWName'))
    FixedRecord['NameEN'] = Auxiliary_NormalizeDisplayTitle(FixedRecord.get('NameEN') or FixedRecord.get('RAWNameEN') or '')
    FixedRecord['NameRomaji'] = Auxiliary_NormalizeDisplayTitle(FixedRecord.get('NameRomaji') or FixedRecord.get('RAWNameRomaji') or '')
    FixedRecord['CanonicalID'] = str(FixedRecord.get('CanonicalID') or '')
    ChangedFlag = False

    CanonicalZh, CanonicalID, _ = Auxiliary_ResolveCanonicalTitleByAliases(
        FixedRecord.get('RAWName'),
        FixedRecord.get('NameEN'),
        FixedRecord.get('NameRomaji'),
    )
    if CanonicalZh in [None, '']:
        CachedTitle = Auxiliary_GetStandardTitleFromCache(
            FixedRecord.get('RAWName') or FixedRecord.get('NameEN') or FixedRecord.get('NameRomaji')
        )
        if CachedTitle not in [None, '']:
            CanonicalZh = CachedTitle
    if CanonicalZh not in [None, ''] and CanonicalZh != FixedRecord.get('RAWName'):
        FixedRecord['RAWName'] = CanonicalZh
        ChangedFlag = True
    if CanonicalID not in [None, ''] and CanonicalID != FixedRecord.get('CanonicalID'):
        FixedRecord['CanonicalID'] = CanonicalID
        ChangedFlag = True
    UpsertCanonicalID, UpsertCanonicalZh = Auxiliary_UpsertCanonicalTitle(
        FixedRecord.get('RAWName', ''),
        FixedRecord.get('NameEN', ''),
        FixedRecord.get('NameRomaji', ''),
        'openai_identify',
        [FixedRecord.get('RAWName'), FixedRecord.get('NameEN'), FixedRecord.get('NameRomaji')],
    )
    if UpsertCanonicalID not in [None, ''] and FixedRecord.get('CanonicalID') != UpsertCanonicalID:
        FixedRecord['CanonicalID'] = UpsertCanonicalID
        ChangedFlag = True
    if UpsertCanonicalZh not in [None, ''] and FixedRecord.get('RAWName') != UpsertCanonicalZh:
        FixedRecord['RAWName'] = UpsertCanonicalZh
        ChangedFlag = True
    ContractedZh = Auxiliary_ContractJujutsuKaisenChineseTitle(FixedRecord.get('RAWName', ''))
    if ContractedZh not in [None, ''] and ContractedZh != FixedRecord.get('RAWName'):
        FixedRecord['RAWName'] = ContractedZh
        ChangedFlag = True
        ReUpsertID, ReUpsertZh = Auxiliary_UpsertCanonicalTitle(
            ContractedZh,
            FixedRecord.get('NameEN', ''),
            FixedRecord.get('NameRomaji', ''),
            'openai_identify',
            [ContractedZh, FixedRecord.get('NameEN', ''), FixedRecord.get('NameRomaji', '')],
        )
        if ReUpsertID not in [None, '']:
            FixedRecord['CanonicalID'] = ReUpsertID
            ChangedFlag = True
        if ReUpsertZh not in [None, ''] and ReUpsertZh != FixedRecord.get('RAWName'):
            FixedRecord['RAWName'] = ReUpsertZh
            ChangedFlag = True
    RemapTuple = Auxiliary_RemappedJujutsuKaisenSeasonEpisode(
        FixedRecord.get('RAWSE'),
        FixedRecord.get('RAWEP'),
        FixedRecord.get('SE'),
        FixedRecord.get('EP'),
        FixedRecord.get('NameEN', ''),
        FixedRecord.get('NameRomaji', ''),
        FixedRecord.get('RAWName', ''),
    )
    if RemapTuple is not None:
        NewRAWSE, NewRAWEP, NewSE, NewEP = RemapTuple
        if (
            NewRAWSE != str(FixedRecord.get('RAWSE', ''))
            or NewRAWEP != str(FixedRecord.get('RAWEP', ''))
            or NewSE != str(FixedRecord.get('SE', ''))
            or NewEP != str(FixedRecord.get('EP', ''))
        ):
            FixedRecord['RAWSE'] = NewRAWSE
            FixedRecord['RAWEP'] = NewRAWEP
            FixedRecord['SE'] = NewSE
            FixedRecord['EP'] = NewEP
            ChangedFlag = True
    return FixedRecord, ChangedFlag


def Auxiliary_ShouldCacheResolvedFileInfo(OperationResult):
    if type(OperationResult) != dict:
        return False
    Status = OperationResult.get('status')
    Message = OperationResult.get('message')
    if Status == 'success':
        return True
    if Status == 'dry-run':
        return True
    if Status == 'skipped' and Message in ['same_file', 'existing_link_kept', 'target_exists', 'newer_duplicate_kept_oldest']:
        return True
    return False
