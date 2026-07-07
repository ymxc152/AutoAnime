"""
autoanime 标准化剧名索引

对应原 `AutoAnimeMv.py`:
- `Auxiliary_GetTitleSourcePriority`
- `Auxiliary_ShouldPreferChineseTitle`
- `Auxiliary_ShouldPreferShorterJujutsuMainTitle`
- `Auxiliary_IsJujutsuKaisenSeries`
- `Auxiliary_ContractJujutsuKaisenChineseTitle`
- `Auxiliary_GetAliasCanonicalID`
- `Auxiliary_GetCanonicalTitleRecord`
- `Auxiliary_LinkAliasToCanonical`
- `Auxiliary_ResolveCanonicalTitleByAliases`
- `Auxiliary_UpsertCanonicalTitle`
"""

from time import localtime, strftime, time

from .. import state
from ..config_loader import Auxiliary_ParseInt
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeAliasKey,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)
from .audit import Auxiliary_AppendPollutionAudit
from .persistent import (
    Auxiliary_GetPersistentCache,
    Auxiliary_SetPersistentCache,
    Auxiliary_SetPersistentCacheAliasWithMeta,
)
from .trust import Auxiliary_TrustLevelFromSource, Auxiliary_ValidateAliasWrite


def Auxiliary_GetTitleSourcePriority(SourceTag):
    SourceTag = '' if SourceTag in [None, ''] else str(SourceTag)
    PriorityMap = {
        'manual': 100,
        'Bangumi': 95,
        'BGM': 90,
        'TMDB': 80,
        'openai_identify': 75,
        'OpenAI': 70,
        'legacy': 50,
        'unknown': 40,
    }
    return PriorityMap.get(SourceTag, 45)


def Auxiliary_ShouldPreferShorterJujutsuMainTitle(OldTitle, NewTitle):
    OldTitle = Auxiliary_NormalizeDisplayTitle(OldTitle)
    NewTitle = Auxiliary_NormalizeDisplayTitle(NewTitle)
    if NewTitle != '咒术回战':
        return False
    if Auxiliary_HasChineseText(OldTitle) == False or ('咒术' in OldTitle and '回战' in OldTitle) == False:
        return False
    if any(Fragment in OldTitle for Fragment in ('怀玉', '玉折', '涩谷', '渋谷', '死灭')):
        return True
    return False


def Auxiliary_ShouldPreferChineseTitle(OldTitle, NewTitle, OldSource='unknown', NewSource='unknown'):
    NewTitle = Auxiliary_NormalizeDisplayTitle(NewTitle)
    OldTitle = Auxiliary_NormalizeDisplayTitle(OldTitle)
    if NewTitle == '':
        return False
    if NewTitle in ['未知', '无法识别', '无法判断', '不确定']:
        return False
    OldHasChinese = Auxiliary_HasChineseText(OldTitle)
    NewHasChinese = Auxiliary_HasChineseText(NewTitle)
    if NewHasChinese == False:
        return False
    if OldTitle == '':
        return True
    if NewHasChinese and OldHasChinese == False:
        return True
    if NewHasChinese == False and OldHasChinese:
        return False
    if OldTitle in ['未知', '无法识别', '无法判断', '不确定']:
        return True
    NewPriority = Auxiliary_GetTitleSourcePriority(NewSource)
    OldPriority = Auxiliary_GetTitleSourcePriority(OldSource)
    if NewPriority >= OldPriority + 5 and NewTitle != OldTitle:
        return True
    if NewHasChinese and OldHasChinese and len(NewTitle) >= len(OldTitle) + 3:
        return True
    if Auxiliary_ShouldPreferShorterJujutsuMainTitle(OldTitle, NewTitle):
        return True
    return False


def Auxiliary_IsJujutsuKaisenSeries(NameEN='', NameRomaji='', NameZH=''):
    Key = Auxiliary_NormalizeAliasKey(NameEN or NameRomaji or '')
    if Key == 'jujutsukaisen':
        return True
    Zh = NameZH or ''
    if Auxiliary_HasChineseText(Zh) and '咒术' in Zh and '回战' in Zh:
        return True
    return False


def Auxiliary_ContractJujutsuKaisenChineseTitle(ChineseTitle):
    ChineseTitle = Auxiliary_NormalizeApiTitle(ChineseTitle)
    if ChineseTitle in [None, ''] or Auxiliary_HasChineseText(ChineseTitle) == False:
        return ChineseTitle
    if ('咒术' in ChineseTitle and '回战' in ChineseTitle) == False:
        return ChineseTitle
    if any(Fragment in ChineseTitle for Fragment in ('怀玉', '玉折', '涩谷', '渋谷', '死灭')):
        return '咒术回战'
    return ChineseTitle


def Auxiliary_GetAliasCanonicalID(AliasTitle):
    AliasKey = Auxiliary_NormalizeAliasKey(AliasTitle)
    if AliasKey == '':
        return None
    if AliasKey in state.TitleAliasIndexDataCache:
        return state.TitleAliasIndexDataCache[AliasKey]
    CanonicalID = Auxiliary_GetPersistentCache('TitleAliasIndex', AliasKey)
    if CanonicalID not in [None, '']:
        state.TitleAliasIndexDataCache[AliasKey] = CanonicalID
        return CanonicalID
    return None


def Auxiliary_GetCanonicalTitleRecord(CanonicalID):
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return None
    Record = None
    if CanonicalID in state.CanonicalTitleIndexDataCache:
        Record = state.CanonicalTitleIndexDataCache.get(CanonicalID)
    else:
        Record = Auxiliary_GetPersistentCache('CanonicalTitleIndex', CanonicalID)
        if Record not in [None, '']:
            state.CanonicalTitleIndexDataCache[CanonicalID] = Record
    if type(Record) != dict:
        return None
    FixedRecord = {
        'zh': Auxiliary_NormalizeDisplayTitle(Record.get('zh', '')),
        'en': Auxiliary_NormalizeDisplayTitle(Record.get('en', '')),
        'romaji': Auxiliary_NormalizeDisplayTitle(Record.get('romaji', '')),
        'source': str(Record.get('source', 'unknown')),
        'last_updated': str(Record.get('last_updated', '')),
        'confidence': Auxiliary_ParseInt(Record.get('confidence', 0), 0),
        'locked': bool(Record.get('locked', False)),
    }
    return FixedRecord


def Auxiliary_LinkAliasToCanonical(
    AliasTitle, CanonicalID, SourceTag: str = 'unknown', trust_level: int = None, conflict: bool = False
):
    AliasKey = Auxiliary_NormalizeAliasKey(AliasTitle)
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if AliasKey == '' or CanonicalID == '':
        return
    if trust_level is None:
        trust_level = Auxiliary_TrustLevelFromSource(str(SourceTag), conflict=bool(conflict))
    ok, reason = Auxiliary_ValidateAliasWrite(AliasKey, CanonicalID, int(trust_level), new_source=str(SourceTag or ''))
    if ok is not True:
        if reason == "same_canonical_higher_trust_noop":
            return
        Auxiliary_AppendPollutionAudit('alias_rejected', {
            'alias_key': AliasKey,
            'canonical_id': CanonicalID,
            'reason': reason,
            'source': str(SourceTag or ''),
            'trust_level': int(trust_level),
        })
        return
    if state.TitleAliasIndexDataCache.get(AliasKey) == CanonicalID:
        # 仍可能需更新 trust，此处仅同 id 无变更则跳过
        return
    state.TitleAliasIndexDataCache[AliasKey] = CanonicalID
    ts = strftime("%Y-%m-%d %H:%M:%S", localtime(time()))
    Auxiliary_SetPersistentCacheAliasWithMeta(
        AliasKey,
        CanonicalID,
        trust_level=int(trust_level),
        source=str(SourceTag or ''),
        added_at=ts,
    )
    Auxiliary_AppendPollutionAudit("alias_written", {
        "alias_key": AliasKey,
        "canonical_id": CanonicalID,
        "source": str(SourceTag or ""),
        "trust_level": int(trust_level),
    })


def Auxiliary_ResolveCanonicalTitleByAliases(*AliasTitleList):
    CheckedAliasSet = set()
    FallbackCanonicalID = None
    FallbackCanonicalRecord = None
    for AliasTitle in AliasTitleList:
        AliasKey = Auxiliary_NormalizeAliasKey(AliasTitle)
        if AliasKey == '' or AliasKey in CheckedAliasSet:
            continue
        CheckedAliasSet.add(AliasKey)
        CanonicalID = Auxiliary_GetAliasCanonicalID(AliasTitle)
        if CanonicalID in [None, '']:
            continue
        CanonicalRecord = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
        if type(CanonicalRecord) != dict:
            continue
        if FallbackCanonicalID in [None, '']:
            FallbackCanonicalID = CanonicalID
            FallbackCanonicalRecord = CanonicalRecord
        CanonicalZh = Auxiliary_NormalizeApiTitle(CanonicalRecord.get('zh', ''))
        if CanonicalZh not in [None, '']:
            return CanonicalZh, CanonicalID, CanonicalRecord
    if FallbackCanonicalID not in [None, '']:
        return None, FallbackCanonicalID, FallbackCanonicalRecord
    return None, None, None



def _Auxiliary_FindNormalizedCanonicalID(ChineseTitle):
    '''在已有 CanonicalTitleIndex 中查找规范化后精确匹配的条目'''
    if ChineseTitle in [None, '']:
        return None
    NormalizedTarget = Auxiliary_NormalizeAliasKey(ChineseTitle)
    if NormalizedTarget == '':
        return None
    for CID, Record in state.CanonicalTitleIndexDataCache.items():
        if type(Record) == dict:
            ExistingZh = Record.get('zh', '')
            NormalizedExisting = Auxiliary_NormalizeAliasKey(ExistingZh)
            if NormalizedExisting != '' and NormalizedExisting == NormalizedTarget:
                return CID
    return None
def Auxiliary_UpsertCanonicalTitle(ChineseTitle='', EnglishTitle='', RomajiTitle='', SourceTag='unknown', AliasList=None):
    ChineseTitle = Auxiliary_NormalizeApiTitle(ChineseTitle)
    EnglishTitle = Auxiliary_NormalizeDisplayTitle(EnglishTitle)
    RomajiTitle = Auxiliary_NormalizeDisplayTitle(RomajiTitle)
    AllAliases = [ChineseTitle, EnglishTitle, RomajiTitle]
    if type(AliasList) in [list, tuple]:
        for OneAlias in AliasList:
            if OneAlias not in [None, '']:
                AllAliases.append(Auxiliary_NormalizeDisplayTitle(OneAlias))
    CandidateCanonicalIDs = []
    for AliasTitle in AllAliases:
        if (MatchedCanonicalID := Auxiliary_GetAliasCanonicalID(AliasTitle)) not in [None, '']:
            if MatchedCanonicalID not in CandidateCanonicalIDs:
                CandidateCanonicalIDs.append(MatchedCanonicalID)
    if CandidateCanonicalIDs == []:
        # 规范化精确匹配：查找已有条目中标题规范化后一致的
        NormalizedCID = _Auxiliary_FindNormalizedCanonicalID(ChineseTitle)
        if NormalizedCID not in [None, '']:
            CandidateCanonicalIDs = [NormalizedCID]
        else:
            SeedTitle = ChineseTitle if ChineseTitle not in [None, ''] else (EnglishTitle if EnglishTitle not in [None, ''] else RomajiTitle)
            CanonicalID = Auxiliary_NormalizeAliasKey(SeedTitle)
            if CanonicalID in [None, '']:
                return None, ChineseTitle
    if CandidateCanonicalIDs != []:
        CanonicalID = CandidateCanonicalIDs[0]
        BestRecord = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
        for OneCanonicalID in CandidateCanonicalIDs[1:]:
            OneRecord = Auxiliary_GetCanonicalTitleRecord(OneCanonicalID)
            if type(OneRecord) == dict and type(BestRecord) == dict:
                if Auxiliary_HasChineseText(OneRecord.get('zh', '')) and Auxiliary_HasChineseText(BestRecord.get('zh', '')) == False:
                    CanonicalID = OneCanonicalID
                    BestRecord = OneRecord
    ExistingRecord = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
    if type(ExistingRecord) != dict:
        CanonicalRecord = {
            'zh': '',
            'en': '',
            'romaji': '',
            'source': 'unknown',
            'last_updated': '',
            'confidence': 0,
            'locked': False,
        }
        ChangedFlag = True
    else:
        CanonicalRecord = ExistingRecord.copy()
        ChangedFlag = False
    if Auxiliary_ShouldPreferChineseTitle(CanonicalRecord.get('zh', ''), ChineseTitle, CanonicalRecord.get('source', 'unknown'), SourceTag):
        CanonicalRecord['zh'] = ChineseTitle
        CanonicalRecord['source'] = SourceTag
        ChangedFlag = True
    elif CanonicalRecord.get('source', '') in [None, '']:
        CanonicalRecord['source'] = SourceTag
        ChangedFlag = True
    if EnglishTitle not in [None, ''] and CanonicalRecord.get('en', '') in [None, '']:
        CanonicalRecord['en'] = EnglishTitle
        ChangedFlag = True
    if RomajiTitle not in [None, ''] and CanonicalRecord.get('romaji', '') in [None, '']:
        CanonicalRecord['romaji'] = RomajiTitle
        ChangedFlag = True
    NewConfidence = max(
        Auxiliary_ParseInt(CanonicalRecord.get('confidence', 0), 0),
        Auxiliary_GetTitleSourcePriority(SourceTag),
    )
    if NewConfidence != Auxiliary_ParseInt(CanonicalRecord.get('confidence', 0), 0):
        CanonicalRecord['confidence'] = NewConfidence
        ChangedFlag = True
    if ChangedFlag:
        CanonicalRecord['last_updated'] = strftime("%Y-%m-%d %H:%M:%S", localtime(time()))
    state.CanonicalTitleIndexDataCache[CanonicalID] = CanonicalRecord
    if ChangedFlag:
        Auxiliary_SetPersistentCache('CanonicalTitleIndex', CanonicalID, CanonicalRecord)
    SeenAliasKeys = set()
    for OneAlias in AllAliases + [CanonicalRecord.get('zh', ''), CanonicalRecord.get('en', ''), CanonicalRecord.get('romaji', '')]:
        ak = Auxiliary_NormalizeAliasKey(OneAlias)
        if ak == '' or ak in SeenAliasKeys:
            continue
        SeenAliasKeys.add(ak)
        Auxiliary_LinkAliasToCanonical(OneAlias, CanonicalID, SourceTag=SourceTag)
    return CanonicalID, CanonicalRecord.get('zh', '')
