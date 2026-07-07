"""
autoanime 剧集/剧季规则

对应原 `AutoAnimeMv.py`:
- `Auxiliary_IDE_ParseSeasonTokensFromFile`
- `Auxiliary_NormalizeEpisodeToken`
- `Auxiliary_CoalesceEpisodeFromParsed` / `Auxiliary_CoalesceSeasonFromParsed`
- `Auxiliary_RemappedJujutsuKaisenSeasonEpisode`
- `Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode`
- `Auxiliary_RemoveEpisodeSuffixFromTitle`
- `Auxiliary_PreDetectEpisodeHint`
- `Auxiliary_BuildEpisodeDecisionKey`
- `Auxiliary_GetAbsoluteSourcePath` / `Auxiliary_GetSourceFileMTime`
"""

from os import path
from pathlib import Path as PathlibPath
from re import I, findall, match, search, sub

from .. import state
from ..logging_utils import Auxiliary_Log
from ..naming import (
    Auxiliary_AnimeFileCheck,
    Auxiliary_FormatSEEPToken,
    Auxiliary_IDEEP,
    Auxiliary_RMOTSTR,
    Auxiliary_RMSubtitlingTeam,
    Auxiliary_UniformOTSTR,
)
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeAliasKey,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)


def Auxiliary_IDE_ParseSeasonTokensFromFile(File):
    '''仅从文件名解析季号，不截断剧名。返回 (SE, RAWSE, RomanSeasonToken)'''
    SeasonMatchData = r'(季(.*?)第)|(([0-9]{0,1}[0-9]{1})S)|(([0-9]{0,1}[0-9]{1})nosaeS)|(([0-9]{0,1}[0-9]{1}) nosaeS)|(([0-9]{0,1}[0-9]{1})-nosaeS)|(nosaeS-dn([0-9]{1}))|(nosaeS-dr([0-9]{1}))'
    SE = None
    RAWSE = ''
    RomanToken = ''
    if (X := findall(SeasonMatchData, File[::-1], flags=I)) != []:
        SEData = X
        SEList = []
        for sedata in SEData:
            for se in sedata:
                if se != '' and se.isnumeric() == False:
                    RomanToken = se[::-1]
                elif se.isnumeric() == True:
                    SEList.append(se)
        for i in range(len(SEList)):
            if SEList[i].isdecimal() == True:
                SE = SEList[i][::-1]
            elif '\u0e00' <= SEList[i] <= '\u9fa5':
                digit = {'一': '01', '二': '02', '三': '03', '四': '04', '五': '05', '六': '06', '七': '07', '八': '08', '九': '09',
                         '壹': '01', '贰': '02', '叁': '03', '肆': '04', '伍': '05', '陆': '06', '柒': '07', '捌': '08', '玖': '09'}
                SE = digit.get(SEList[i], '01')
            if SE is not None:
                RAWSE = str(SE).lstrip('0') or str(SE)
                SE = str(SE)
                return SE, RAWSE, RomanToken
    elif (X := findall(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]', File[::-1], flags=I)) != []:
        A = {'Ⅰ': '01', 'Ⅱ': '02', 'Ⅲ': '03', 'Ⅳ': '04', 'Ⅴ': '05', 'Ⅵ': '06', 'Ⅶ': '07', 'Ⅷ': '08', 'Ⅸ': '09', 'Ⅹ': '10', 'Ⅺ': '11', 'Ⅻ': '12'}
        SE = A[X[0]]
        return SE, str(int(SE)), X[0]
    return '01', '1', ''


def Auxiliary_NormalizeEpisodeToken(RawEpisode, FileName=''):
    RawEpisode = '' if RawEpisode in [None, ''] else str(RawEpisode).strip()
    if RawEpisode == '':
        return '', True
    RawEpisode = RawEpisode.replace('．', '.')
    DecimalMatch = match(r'^([0-9]{1,4})\.([0-9]{1,2})$', RawEpisode)
    if DecimalMatch is not None:
        IntPart = DecimalMatch.group(1)
        DecimalPart = DecimalMatch.group(2)
        if DecimalPart.strip('0') == '':
            RawEpisode = str(int(IntPart))
        elif DecimalPart == '5' and search(r'(?i)v[2-9]', str(FileName)) is not None:
            RawEpisode = str(int(IntPart))
    IsSpecial = (RawEpisode in ['0', '00']) or ('.' in RawEpisode)
    return RawEpisode, IsSpecial


def Auxiliary_CoalesceEpisodeFromParsed(ParsedData):
    '''从模型 JSON 取剧集字段；不能用 `or` 链（episode 为整数 0 时会被当成假值丢弃）'''
    if type(ParsedData) != dict:
        return ''
    for Key in ('episode', 'ep'):
        if Key not in ParsedData:
            continue
        Val = ParsedData[Key]
        if Val is None:
            continue
        Raw = str(Val).strip()
        if Raw != '':
            return Raw
    return ''


def Auxiliary_CoalesceSeasonFromParsed(ParsedData, DefaultSeason='1'):
    if type(ParsedData) != dict:
        return DefaultSeason
    for Key in ('season', 'se'):
        if Key not in ParsedData:
            continue
        Val = ParsedData[Key]
        if Val is None:
            continue
        Raw = str(Val).strip()
        if Raw != '':
            return Raw
    return DefaultSeason


def Auxiliary_RemoveEpisodeSuffixFromTitle(Title, RawEpisode):
    Title = Auxiliary_NormalizeDisplayTitle(Title)
    EpisodeValue, _ = Auxiliary_NormalizeEpisodeToken(RawEpisode)
    if Title == '' or EpisodeValue == '' or EpisodeValue.isdigit() == False:
        return Auxiliary_NormalizeApiTitle(Title)
    EpisodeInt = str(int(EpisodeValue))
    CandidateTitle = Title
    CandidateTitle = sub(rf'[\s\-_]+0*{EpisodeInt}$', '', CandidateTitle, flags=I).strip(' -_')
    CandidateTitle = sub(rf'第\s*0*{EpisodeInt}\s*[话話集]$', '', CandidateTitle, flags=I).strip(' -_')
    CandidateTitle = sub(rf'[\(\[（【]\s*0*{EpisodeInt}\s*[\)\]）】]$', '', CandidateTitle, flags=I).strip(' -_')
    if CandidateTitle not in [None, '']:
        return Auxiliary_NormalizeApiTitle(CandidateTitle)
    return Auxiliary_NormalizeApiTitle(Title)


_MANUAL_SEASON_LAYOUT = {
    '葬送的芙莉莲': [(1, 28), (2, None)],
    'Sousou no Frieren': [(1, 28), (2, None)],
    '地狱乐': [(1, 13)],
    'Jigokuraku': [(1, 13)],
    '咒术回战': [(1, 24), (2, 23), (3, 14)],
    'Jujutsu Kaisen': [(1, 24), (2, 23), (3, 14)],
}

# 预计算归一化键，支持大小写/标点不同的别名命中
_NORMALIZED_MANUAL_SEASON_LAYOUT = {
    Auxiliary_NormalizeAliasKey(k): v for k, v in _MANUAL_SEASON_LAYOUT.items()
}


def _LookupManualSeasonLayout(NameEN='', NameRomaji='', NameZH=''):
    '''根据英文/罗马音/中文剧名查找内置 season layout；找不到返回 None。'''
    for Name in (NameZH, NameEN, NameRomaji):
        if Name in [None, '']:
            continue
        if Name in _MANUAL_SEASON_LAYOUT:
            return _MANUAL_SEASON_LAYOUT[Name]
        Key = Auxiliary_NormalizeAliasKey(Name)
        if Key in _NORMALIZED_MANUAL_SEASON_LAYOUT:
            return _NORMALIZED_MANUAL_SEASON_LAYOUT[Key]
    return None


def _FormatRemappedSEEPTokens(NewRAWSE, NewRAWEP):
    '''把原始季/集数字格式化为 SE/EP（受 SEEPSINGLECHARACTER 影响）。'''
    NewSE = NewRAWSE.zfill(2) if state.SEEPSINGLECHARACTER == False else NewRAWSE.lstrip('0')
    if NewSE in [None, '']:
        NewSE = '1' if state.SEEPSINGLECHARACTER == True else '01'
    NewEP = '0' + NewRAWEP if (len(NewRAWEP) < 2 or ('.' in NewRAWEP and NewRAWEP[0] != '0')) and (state.SEEPSINGLECHARACTER == False) else NewRAWEP
    if state.SEEPSINGLECHARACTER == True:
        NewSE = NewSE.lstrip('0')
        NewEP = NewEP.lstrip('0')
        NewSE = NewSE if NewSE not in [None, ''] else '0'
        NewEP = NewEP if NewEP not in [None, ''] else '0'
    return NewSE, NewEP


def Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
    RAWSE, RAWEP, SE, EP, NameEN, NameRomaji, NameZH, SeasonPairs=None
):
    '''把长篇/多季番的「绝对集数」映射到「季内集数」。

    例如：葬送的芙莉莲 S1=28 集，绝对集号 38 -> S02E10。

    参数：
        RAWSE/RAWEP：从文件名解析出的原始季/集字符串。
        SE/EP：格式化后的季/集字符串。
        NameEN/NameRomaji/NameZH：剧名线索，用于命中内置/外部 season layout。
        SeasonPairs：可选，[(season_number, episode_count), ...]；None 时自动查找。
            其中 episode_count 可为 None，表示该季长度未知（允许向后 extrapolate）。

    返回：
        (NewRAWSE, NewRAWEP, NewSE, NewEP) 或 None（无需映射 / 越界）。
    '''
    FromManual = False
    if SeasonPairs is None:
        SeasonPairs = _LookupManualSeasonLayout(NameEN, NameRomaji, NameZH)
        FromManual = True
    # 对咒术回战保持与旧函数一致：优先尝试 TMDB layout
    if not SeasonPairs:
        from ..cache.canonical import Auxiliary_IsJujutsuKaisenSeries
        if Auxiliary_IsJujutsuKaisenSeries(NameEN, NameRomaji, NameZH):
            from ..apis.tmdb import (
                Auxiliary_GetTMDBTvSeasonLayoutBySeriesId,
                Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout,
                Auxiliary_ResolveTMDBTvIdForJujutsuKaisen,
            )
            TvId = Auxiliary_ResolveTMDBTvIdForJujutsuKaisen(NameEN, NameRomaji)
            if TvId not in [None, '']:
                SeasonPairs = Auxiliary_GetTMDBTvSeasonLayoutBySeriesId(TvId)

    if not SeasonPairs:
        return None

    RAWEP = str(RAWEP or '').strip()
    if RAWEP == '' or RAWEP.split('.')[0].isdigit() == False:
        return None
    AbsEp = int(RAWEP.split('.')[0])
    CurrentSeason = int(str(RAWSE or '1').strip() or '1')

    # 若文件名已显式指定非首季，信任该季号，不再做绝对集数映射
    if CurrentSeason > 1:
        return None

    # 计算各季累计边界；None 表示该季长度未知
    Boundaries = []
    Cumulative = 0
    LastFiniteCumulative = 0
    for SeasonNum, EpCount in SeasonPairs:
        if EpCount is None:
            Boundaries.append({'season': int(SeasonNum), 'cumulative': None, 'count': None})
            continue
        Cumulative += int(EpCount)
        Boundaries.append({'season': int(SeasonNum), 'cumulative': Cumulative, 'count': int(EpCount)})
        LastFiniteCumulative = Cumulative

    # 绝对集号落在首季范围内，无需映射
    if Boundaries and Boundaries[0]['cumulative'] is not None and AbsEp <= Boundaries[0]['cumulative']:
        return None

    # 超出已知有限季总集数时：
    # - manual 表且没有开放季尾巴：越界告警；
    # - 调用方显式传入 SeasonPairs 且没有开放季尾巴：extrapolate 到下一季；
    # - 含开放季尾巴：交给下方循环处理。
    if LastFiniteCumulative and AbsEp > LastFiniteCumulative:
        HasOpenEndedTail = any(b['cumulative'] is None for b in Boundaries)
        if FromManual and not HasOpenEndedTail:
            ShowName = NameZH or NameEN or NameRomaji or '未知番剧'
            Auxiliary_Log(
                f'绝对集数映射：{ShowName} EP={AbsEp} 超出已知正片范围（共 {LastFiniteCumulative} 集），放弃映射',
                'WARNING',
            )
            return None
        if not HasOpenEndedTail:
            # extrapolate 到已定义最后一季的下一季
            LastSeasonNum = Boundaries[-1]['season'] if Boundaries else 1
            NewRAWSE = str(LastSeasonNum + 1)
            NewRAWEP = str(AbsEp - LastFiniteCumulative)
            NewSE, NewEP = _FormatRemappedSEEPTokens(NewRAWSE, NewRAWEP)
            ShowName = NameZH or NameEN or NameRomaji or '未知番剧'
            Auxiliary_Log(f'绝对集数映射：{ShowName} EP={AbsEp} -> S{NewSE}E{NewEP}', 'INFO')
            return NewRAWSE, NewRAWEP, NewSE, NewEP

    # 定位 AbsEp 落在哪一季
    PrevCumulative = 0
    for Boundary in Boundaries:
        SeasonNum = Boundary['season']
        CumulativeEps = Boundary['cumulative']
        EpCount = Boundary['count']
        if CumulativeEps is None:
            # 开放季：从前一季末尾继续累加
            NewEpInSeason = AbsEp - PrevCumulative
            NewRAWSE = str(SeasonNum)
            NewRAWEP = str(int(NewEpInSeason))
            NewSE, NewEP = _FormatRemappedSEEPTokens(NewRAWSE, NewRAWEP)
            ShowName = NameZH or NameEN or NameRomaji or '未知番剧'
            Auxiliary_Log(f'绝对集数映射：{ShowName} EP={AbsEp} -> S{NewSE}E{NewEP}', 'INFO')
            return NewRAWSE, NewRAWEP, NewSE, NewEP
        if AbsEp <= CumulativeEps:
            NewEpInSeason = AbsEp - (CumulativeEps - EpCount)
            NewRAWSE = str(SeasonNum)
            NewRAWEP = str(int(NewEpInSeason))
            NewSE, NewEP = _FormatRemappedSEEPTokens(NewRAWSE, NewRAWEP)
            ShowName = NameZH or NameEN or NameRomaji or '未知番剧'
            Auxiliary_Log(f'绝对集数映射：{ShowName} EP={AbsEp} -> S{NewSE}E{NewEP}', 'INFO')
            return NewRAWSE, NewRAWEP, NewSE, NewEP
        PrevCumulative = CumulativeEps

    return None


def Auxiliary_RemappedJujutsuKaisenSeasonEpisode(RAWSE, RAWEP, SE, EP, NameEN, NameRomaji, NameZH):
    from ..apis.tmdb import (
        Auxiliary_GetTMDBTvSeasonLayoutBySeriesId,
        Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout,
        Auxiliary_ResolveTMDBTvIdForJujutsuKaisen,
    )
    from ..cache.canonical import Auxiliary_IsJujutsuKaisenSeries

    if Auxiliary_IsJujutsuKaisenSeries(NameEN, NameRomaji, NameZH) == False:
        return None
    RAWEP = str(RAWEP or '').strip()
    if RAWEP == '' or RAWEP.split('.')[0].isdigit() == False:
        return None
    AbsEp = int(RAWEP.split('.')[0])
    SeasonPairs = []
    TvId = Auxiliary_ResolveTMDBTvIdForJujutsuKaisen(NameEN, NameRomaji)
    if TvId not in [None, '']:
        SeasonPairs = Auxiliary_GetTMDBTvSeasonLayoutBySeriesId(TvId)
    FirstSeasonCap = SeasonPairs[0][1] if SeasonPairs else 24
    if AbsEp <= FirstSeasonCap:
        return None
    Mapped = Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(AbsEp, SeasonPairs) if SeasonPairs else None
    if Mapped is None:
        if AbsEp <= 47:
            NewRAWSE = '2'
            NewRAWEP = str(AbsEp - 24)
        else:
            NewRAWSE = '3'
            NewRAWEP = str(AbsEp - 47)
    else:
        NewSeasonNum, NewEpInSeason = Mapped
        NewRAWSE = str(int(NewSeasonNum))
        NewRAWEP = str(int(NewEpInSeason))
    NewSE, NewEP = _FormatRemappedSEEPTokens(NewRAWSE, NewRAWEP)
    return NewRAWSE, NewRAWEP, NewSE, NewEP


def Auxiliary_GetAbsoluteSourcePath(SourceFilePath):
    SourceFilePath = '' if SourceFilePath in [None, ''] else str(SourceFilePath)
    SourcePathObj = PathlibPath(SourceFilePath)
    if SourcePathObj.is_absolute():
        return SourcePathObj
    BasePath = PathlibPath(state.Path) if state.Path not in [None, ''] else (
        state.Runtime.source_path if state.Runtime else PathlibPath('.')
    )
    return BasePath / SourcePathObj


def Auxiliary_GetSourceFileMTime(SourceFilePath):
    SourcePathObj = Auxiliary_GetAbsoluteSourcePath(SourceFilePath)
    try:
        return float(SourcePathObj.stat().st_mtime)
    except Exception:
        return 0.0


def Auxiliary_BuildEpisodeDecisionKey(CanonicalTitle, SE, EP, FileName):
    CanonicalAliasKey = Auxiliary_NormalizeAliasKey(CanonicalTitle)
    if CanonicalAliasKey == '':
        return None
    SEValue = Auxiliary_FormatSEEPToken(SE)
    EPValue = Auxiliary_FormatSEEPToken(EP)
    FileExt = str(path.splitext(path.basename(str(FileName)))[1]).lower()
    if FileExt in ['.mp4', '.mkv']:
        ExtBucket = 'video'
    elif FileExt in ['.ass', '.srt']:
        ExtBucket = 'subtitle'
    else:
        ExtBucket = FileExt if FileExt not in [None, ''] else 'unknown'
    return f'{CanonicalAliasKey}|{SEValue}|{EPValue}|{ExtBucket}'


def Auxiliary_PreDetectEpisodeHint(FileName):
    from ..cache.canonical import Auxiliary_ResolveCanonicalTitleByAliases

    QueryName = path.basename(str(FileName))
    NewFile = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(QueryName)))
    if Auxiliary_AnimeFileCheck(NewFile) != True:
        return None
    try:
        RAWEP = Auxiliary_IDEEP(NewFile)
    except Exception:
        return None
    RAWEP, EpisodeSpecialFlag = Auxiliary_NormalizeEpisodeToken(RAWEP, QueryName)
    if RAWEP in [None, '']:
        return None
    BaseTitle = path.splitext(NewFile)[0]
    RAWName = Auxiliary_NormalizeApiTitle(BaseTitle)
    EP = '0' + RAWEP if (len(RAWEP) < 2 or ('.' in RAWEP and RAWEP[0] != '0')) and (state.SEEPSINGLECHARACTER == False) else RAWEP
    if EpisodeSpecialFlag:
        SE = '00' if state.SEEPSINGLECHARACTER == False else '0'
        RAWSE = ''
    else:
        SERaw, RSE, _ = Auxiliary_IDE_ParseSeasonTokensFromFile(NewFile)
        SE = '0' + str(SERaw) if len(str(SERaw)) == 1 and state.SEEPSINGLECHARACTER == False else str(SERaw)
        RAWSE = RSE
    if state.SEEPSINGLECHARACTER == True:
        SE = SE.lstrip('0')
        EP = EP.lstrip('0')
        SE = SE if SE not in [None, ''] else '0'
        EP = EP if EP not in [None, ''] else '0'
    CanonicalZh, CanonicalID, _ = Auxiliary_ResolveCanonicalTitleByAliases(RAWName)
    CanonicalTitle = CanonicalZh if CanonicalZh not in [None, ''] else RAWName
    EpisodeKey = Auxiliary_BuildEpisodeDecisionKey(CanonicalTitle, SE, EP, QueryName)
    if EpisodeKey in [None, '']:
        return None
    return {
        'EpisodeKey': EpisodeKey,
        'SE': str(SE),
        'EP': str(EP),
        'RAWName': RAWName,
        'ApiName': CanonicalTitle,
        'CanonicalID': CanonicalID if CanonicalID not in [None, ''] else '',
    }
