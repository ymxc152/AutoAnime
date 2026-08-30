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
    Auxiliary_DelPersistentCacheAlias,
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
def _Auxiliary_ShouldOverwriteForeignName(OldValue, NewValue, OldSource, NewSource, SameShow):
    """外文名（en/romaji）是否用新值覆盖旧值。

    原逻辑"仅当现有值为空才写"导致错误的 en/romaji 一旦写入就永驻、且污染随
    别名索引扩散。本函数给出可自愈的覆盖策略，同时避免低可信覆盖高可信：

      1. 新值缺失 或 与旧值相同 → 不覆盖（无变化）
      2. 旧值缺失 → 写入
      3. 新来源优先级 > 旧来源优先级 → 覆盖（manual=100 最高，可纠错一切）
      4. 优先级相同且新 zh 与旧 zh 是同一部番 → 覆盖（同级重识别 = 同剧更正）
      5. 其余情况（同级但不同番 / 更低优先级）→ 保留旧值，防止串号污染
    """
    if NewValue in [None, ''] or NewValue == OldValue:
        return False
    if OldValue in [None, '']:
        return True
    NewPriority = Auxiliary_GetTitleSourcePriority(NewSource)
    OldPriority = Auxiliary_GetTitleSourcePriority(OldSource)
    if NewPriority > OldPriority:
        return True
    if NewPriority == OldPriority and SameShow:
        return True
    return False


def _Auxiliary_IsForeignNameForeignOwned(FieldValue, CanonicalID, SourceTag):
    """外文名是否已被「另一部番」的 canonical 认领（作为其别名）。

    例如 romaji "Tenbin" 已被 冷然之天秤 认领，此时把它写到别的 canonical 上
    就是串号污染。manual / manual_title_whitelist 不受此限制（用户明确指定）。
    """
    if FieldValue in [None, '']:
        return False
    if str(SourceTag) in ('manual', 'manual_title_whitelist', 'ManualWhitelist'):
        return False
    AliasKey = Auxiliary_NormalizeAliasKey(FieldValue)
    if AliasKey == '':
        return False
    Owner = Auxiliary_GetAliasCanonicalID(FieldValue)
    if Owner in [None, '']:
        return False
    return str(Owner) != str(CanonicalID)


def _Auxiliary_UnlinkFieldAliasIfOwned(FieldValue, CanonicalID):
    """解除某个外文名作为别名指向 CanonicalID 的链接（仅当确实指向它且不是当前主名）。

    外文名被覆盖后，旧值不应再作为别名指回本 canonical（否则错误外文名仍可通过
    别名索引把后续识别串号）。
    """
    if FieldValue in [None, '']:
        return
    AliasKey = Auxiliary_NormalizeAliasKey(FieldValue)
    if AliasKey == '':
        return
    Rec = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
    if type(Rec) is dict:
        for Field in ('zh', 'en', 'romaji'):
            if Auxiliary_NormalizeAliasKey(Rec.get(Field, '')) == AliasKey:
                return  # 当前主名仍是该别名，保留
    CurrentCID = state.TitleAliasIndexDataCache.get(AliasKey)
    if CurrentCID in [None, '']:
        CurrentCID = Auxiliary_GetAliasCanonicalID(FieldValue)
    if str(CurrentCID) == str(CanonicalID):
        Auxiliary_DelPersistentCacheAlias(AliasKey)
        Auxiliary_AppendPollutionAudit("alias_removed", {
            "alias_key": AliasKey,
            "canonical_id": CanonicalID,
            "reason": "foreign_name_overwritten",
        })


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
    # 预取旧值 / 同剧判定，供外文名自动修正使用
    SameShow = False
    NewZhNorm = Auxiliary_NormalizeApiTitle(ChineseTitle)
    OldZhNorm = Auxiliary_NormalizeApiTitle(CanonicalRecord.get('zh', ''))
    if NewZhNorm not in [None, ''] and OldZhNorm not in [None, ''] and NewZhNorm == OldZhNorm:
        SameShow = True
    RecordSource = CanonicalRecord.get('source', 'unknown')
    OldEn = CanonicalRecord.get('en', '')
    OldRomaji = CanonicalRecord.get('romaji', '')
    if Auxiliary_ShouldPreferChineseTitle(CanonicalRecord.get('zh', ''), ChineseTitle, CanonicalRecord.get('source', 'unknown'), SourceTag):
        CanonicalRecord['zh'] = ChineseTitle
        CanonicalRecord['source'] = SourceTag
        ChangedFlag = True
    elif CanonicalRecord.get('source', '') in [None, '']:
        CanonicalRecord['source'] = SourceTag
        ChangedFlag = True
    if CanonicalRecord.get('locked') is not True:
        WriteEn = (_Auxiliary_ShouldOverwriteForeignName(OldEn, EnglishTitle, RecordSource, SourceTag, SameShow)
                   and not _Auxiliary_IsForeignNameForeignOwned(EnglishTitle, CanonicalID, SourceTag))
        WriteRo = (_Auxiliary_ShouldOverwriteForeignName(OldRomaji, RomajiTitle, RecordSource, SourceTag, SameShow)
                   and not _Auxiliary_IsForeignNameForeignOwned(RomajiTitle, CanonicalID, SourceTag))
        if WriteEn:
            CanonicalRecord['en'] = EnglishTitle
            ChangedFlag = True
        if WriteRo:
            CanonicalRecord['romaji'] = RomajiTitle
            ChangedFlag = True
        # 更高优先级来源修正了外文名时，同步提升记录来源，防止同级/低可信来源再次改回
        if (WriteEn or WriteRo) and Auxiliary_GetTitleSourcePriority(SourceTag) > Auxiliary_GetTitleSourcePriority(RecordSource):
            CanonicalRecord['source'] = SourceTag
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
        if _Auxiliary_IsForeignNameForeignOwned(OneAlias, CanonicalID, SourceTag):
            continue  # 已被另一部番认领的别名，跳过，防止串号扩散
        Auxiliary_LinkAliasToCanonical(OneAlias, CanonicalID, SourceTag=SourceTag)
    # 外文名被覆盖后，解除旧值残留的别名链接（防串号继续通过别名索引扩散）
    if OldEn not in [None, ''] and OldEn != CanonicalRecord.get('en', ''):
        _Auxiliary_UnlinkFieldAliasIfOwned(OldEn, CanonicalID)
    if OldRomaji not in [None, ''] and OldRomaji != CanonicalRecord.get('romaji', ''):
        _Auxiliary_UnlinkFieldAliasIfOwned(OldRomaji, CanonicalID)
    return CanonicalID, CanonicalRecord.get('zh', '')
