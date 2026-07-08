"""
autoanime 本地识别 + 传统 API 回退链路（fix_ai_fallback todo）

当 OpenAI 主识别路径失败时，本模块提供"本地规则 -> BGM -> Bangumi -> TMDB"三路
回退，确保不会因 missing_api_key / 429 / 网络抖动等暂时性故障而整盘跳过。

| 函数 | 作用 | 备注 |
| --- | --- | --- |
| `Auxiliary_FallbackLocalRules`            | 仅用本地 IDESE/IDEEP/IDEVDName 规则抽取 (SE, EP, RAWSE, RAWEP, RAWName) | 不联网 |
| `Auxiliary_FallbackTraditionalApis`       | 在本地规则基础上依次查 BGM -> Bangumi -> TMDB 中文标题 | 命中立即返回 |
| `Auxiliary_ResolveFileInfoWithFallback`   | 对外主入口：AI 失败后的回退编排 | 返回与 `Auxiliary_OpenAIIdentifyFileInfo` 同结构元组 |

熔断：`Auxiliary_ShouldTripOpenAIBreaker` / `Auxiliary_NoteOpenAIBreakerEvent` 记录 401/403/429
连续次数，一旦超过 `OPENAI_FALLBACK_BREAKER_THRESHOLD`（默认 5）后续文件直接走回退链路。
"""

from os import path

from .. import state
from ..logging_utils import Auxiliary_Log
from ..naming import (
    Auxiliary_AnimeFileCheck,
    Auxiliary_IDEEP,
    Auxiliary_IDEVDName,
    Auxiliary_RMOTSTR,
    Auxiliary_RMSubtitlingTeam,
    Auxiliary_UniformOTSTR,
)
from ..cache.manual_whitelist import Auxiliary_GetManualWhitelistedTitle
from ..text_utils import (
    Auxiliary_CleanFallbackTitle,
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)
from .episode_rules import (
    Auxiliary_IDE_ParseSeasonTokensFromFile,
    Auxiliary_NormalizeEpisodeToken,
)


_BREAKER_STATUS_CODES = {'401', '403', '429'}
_BREAKER_DEFAULT_THRESHOLD = 5


def Auxiliary_IsFallbackEnabled() -> bool:
    '''是否启用 AI 失败回退链路。默认 True；可通过 `config.ini` 的 `OPENAI_FALLBACK_ON_FAILURE` 关闭。'''
    val = getattr(state, 'OPENAI_FALLBACK_ON_FAILURE', True)
    if type(val) == bool:
        return val
    return str(val).strip().lower() not in ['false', '0', 'no', 'n', 'off']


def _GetBreakerThreshold() -> int:
    try:
        val = int(getattr(state, 'OPENAI_FALLBACK_BREAKER_THRESHOLD', _BREAKER_DEFAULT_THRESHOLD))
    except Exception:
        val = _BREAKER_DEFAULT_THRESHOLD
    return val if val > 0 else _BREAKER_DEFAULT_THRESHOLD


def Auxiliary_NoteOpenAIBreakerEvent(failure: dict) -> None:
    '''登记一次 AI 识别失败；当失败原因是认证/限流类 401/403/429 时增加熔断计数。'''
    if type(failure) != dict:
        return
    reason = str(failure.get('reason', ''))
    detail = str(failure.get('detail', ''))
    triggered = False
    if reason == 'http_status':
        for code in _BREAKER_STATUS_CODES:
            if f'status={code}' in detail:
                triggered = True
                break
    elif reason == 'missing_api_key':
        triggered = True
    if triggered == False:
        cur = int(getattr(state, 'OpenAIFallbackBreakerStreak', 0) or 0)
        if cur > 0:
            # 只要出现一次非熔断类失败就保留计数（不重置，避免被间歇性非熔断 err 清零）
            pass
        return
    state.OpenAIFallbackBreakerStreak = int(getattr(state, 'OpenAIFallbackBreakerStreak', 0) or 0) + 1


def Auxiliary_ResetOpenAIBreaker() -> None:
    state.OpenAIFallbackBreakerStreak = 0


def Auxiliary_ShouldTripOpenAIBreaker() -> bool:
    '''返回 True 表示已连续累计 N 次熔断类失败，建议本轮后续文件直接跳过 AI、直走回退链路。'''
    cur = int(getattr(state, 'OpenAIFallbackBreakerStreak', 0) or 0)
    return cur >= _GetBreakerThreshold()


# =========================================================================
# 本地规则回退
# =========================================================================
def Auxiliary_FallbackLocalRules(File: str):
    '''只用本地正则规则提取 (SE, EP, RAWSE, RAWEP, RAWName)。

    返回 `(SE, EP, RAWSE, RAWEP, RAWName)` 或 None（剧集抽不出来时）。
    '''
    QueryFileName = path.basename(str(File))
    NewFile = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(QueryFileName)))
    if Auxiliary_AnimeFileCheck(NewFile) != True:
        return None
    try:
        RAWEP = Auxiliary_IDEEP(NewFile)
    except Exception:
        Auxiliary_Log(f'本地回退识别失败：无法抽取剧集 {QueryFileName}', 'WARNING')
        return None
    RAWEP, SpecialFlag = Auxiliary_NormalizeEpisodeToken(RAWEP, QueryFileName)
    if RAWEP in [None, '']:
        return None

    if SpecialFlag:
        SE = '00' if state.SEEPSINGLECHARACTER == False else '0'
        RAWSE = ''
    else:
        SERaw, RSE, _ = Auxiliary_IDE_ParseSeasonTokensFromFile(NewFile)
        SE = '0' + str(SERaw) if len(str(SERaw)) == 1 and state.SEEPSINGLECHARACTER == False else str(SERaw)
        RAWSE = RSE
    EP = '0' + RAWEP if (len(RAWEP) < 2 or ('.' in RAWEP and RAWEP[0] != '0')) and (state.SEEPSINGLECHARACTER == False) else RAWEP
    if state.SEEPSINGLECHARACTER == True:
        SE = SE.lstrip('0') or '0'
        EP = EP.lstrip('0') or '0'

    try:
        RAWName = Auxiliary_IDEVDName(NewFile, RAWEP)
    except Exception:
        RAWName = path.splitext(QueryFileName)[0]
    RAWName = Auxiliary_CleanFallbackTitle(RAWName)
    RAWName = Auxiliary_NormalizeApiTitle(RAWName) if RAWName not in [None, ''] else ''
    if RAWName in [None, '']:
        RAWName = path.splitext(QueryFileName)[0]
    return SE, EP, RAWSE, RAWEP, RAWName


def _StripSeasonSuffixes(Name):
    '''剥离常见季号后缀，生成更简洁的 Bangumi/TMDB 查询候选。'''
    from re import I, sub

    Name = '' if Name in [None, ''] else str(Name).strip()
    if Name == '':
        return ''
    # 先走现有归一化，去掉 "Season X" / "SX" / "第X季" 等
    Name = Auxiliary_NormalizeApiTitle(Name)
    # 再补充剥离 "Xnd Season" / "Xst Season" / "Xrd Season" / "Xth Season"
    # 兼容空格或连字符连接："2nd Season" / "2nd-Season"
    Name = sub(r'[0-9]{1,3}(st|nd|rd|th)[\s\-]*season$', '', Name, flags=I).strip()
    # 处理可能残留的 "S X" / "S-X" / "SX" 结尾
    Name = sub(r'[\s\-]+s[\s\-]*[0-9]{1,3}$', '', Name, flags=I).strip()
    return Name.strip('- []【】 ')


# =========================================================================
# 本地规则 + 传统 API 三路回退
# =========================================================================
def _GenerateQueryNameCandidates(RAWNameLocal):
    '''生成 Bangumi/TMDB 查询候选列表，优先尝试剥离季号后的名称。'''
    Candidates = []
    BaseName = Auxiliary_NormalizeDisplayTitle(RAWNameLocal)
    if BaseName not in [None, ''] and BaseName not in Candidates:
        Candidates.append(BaseName)
    Stripped = _StripSeasonSuffixes(BaseName)
    if Stripped not in [None, ''] and Stripped not in Candidates:
        Candidates.insert(0, Stripped)
    return Candidates
def Auxiliary_FallbackTraditionalApis(LocalBase):
    '''在本地规则基础上查 BGM -> Bangumi -> TMDB 三路中文标题，取最先命中的。

    入参 `LocalBase` 必须是 `Auxiliary_FallbackLocalRules` 的返回元组。
    返回 `(SE, EP, RAWSE, RAWEP, RAWName, NameEN, NameRomaji, CanonicalID)` 或 None。
    '''
    if LocalBase is None:
        return None
    SE, EP, RAWSE, RAWEP, RAWNameLocal = LocalBase
    RAWNameLocal = Auxiliary_NormalizeDisplayTitle(RAWNameLocal)

    ChineseTitle = ''
    SourceTag = ''
    NameEN = ''
    NameRomaji = ''
    CanonicalID = ''

    from ..apis.bgm import Auxiliary_QueryBgmChineseTitle
    from ..apis.bangumi import Auxiliary_QueryBangumiChineseTitle
    from ..apis.tmdb import Auxiliary_QueryTMDBChineseTitle, Auxiliary_QueryTMDBEnglishTitle
    from ..cache.canonical import (
        Auxiliary_GetCanonicalTitleRecord,
        Auxiliary_ResolveCanonicalTitleByAliases,
        Auxiliary_UpsertCanonicalTitle,
    )

    # 先从 Canonical 索引取英文/罗马音线索供各 API 使用
    CachedZh, CachedID, _ = Auxiliary_ResolveCanonicalTitleByAliases(RAWNameLocal)
    if CachedID not in [None, '']:
        CanonicalID = str(CachedID)
        Record = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
        if type(Record) == dict:
            NameEN = Auxiliary_NormalizeDisplayTitle(Record.get('en', ''))
            NameRomaji = Auxiliary_NormalizeDisplayTitle(Record.get('romaji', ''))
    if CachedZh not in [None, ''] and Auxiliary_HasChineseText(CachedZh):
        ChineseTitle = CachedZh
        SourceTag = 'canonical_cache'

    if ChineseTitle in [None, '']:
        QueryCandidates = _GenerateQueryNameCandidates(RAWNameLocal)
        ApiChain = [
            ('BGM', lambda q: Auxiliary_QueryBgmChineseTitle(q, NameEN, NameRomaji)),
            ('Bangumi', lambda q: Auxiliary_QueryBangumiChineseTitle(q, NameEN, NameRomaji)),
            ('TMDB', lambda q: Auxiliary_QueryTMDBChineseTitle(q, NameEN, NameRomaji)),
        ]
        for Tag, Fn in ApiChain:
            for QueryName in QueryCandidates:
                try:
                    Result = Fn(QueryName)
                except Exception as err:
                    Auxiliary_Log(f'回退链路 {Tag} 查询异常（已忽略）：{err}', 'WARNING')
                    break
                Result = Auxiliary_NormalizeApiTitle(Result) if Result not in [None, ''] else ''
                if Result not in [None, ''] and Auxiliary_HasChineseText(Result):
                    ChineseTitle = Result
                    SourceTag = Tag
                    break
            if ChineseTitle not in [None, '']:
                break

    if NameEN in [None, '']:
        try:
            NameEN = Auxiliary_QueryTMDBEnglishTitle(RAWNameLocal) or ''
        except Exception:
            NameEN = ''
        NameEN = Auxiliary_NormalizeDisplayTitle(NameEN)

    if ChineseTitle in [None, '']:
        WhitelistTitle = Auxiliary_GetManualWhitelistedTitle(RAWNameLocal, NameEN, NameRomaji)
        if WhitelistTitle not in [None, '']:
            ChineseTitle = WhitelistTitle
            SourceTag = 'manual_whitelist'
            Auxiliary_Log(f'回退链路命中手工白名单：{RAWNameLocal} -> {ChineseTitle}', 'INFO')

    if ChineseTitle in [None, ''] and NameEN in [None, '']:
        return None

    FinalName = ChineseTitle if ChineseTitle not in [None, ''] else RAWNameLocal
    CanonicalFromUpsert, CanonicalZh = Auxiliary_UpsertCanonicalTitle(
        ChineseTitle,
        NameEN,
        NameRomaji,
        SourceTag if SourceTag not in [None, ''] else 'local_fallback',
        [RAWNameLocal],
    )
    if CanonicalFromUpsert not in [None, '']:
        CanonicalID = CanonicalFromUpsert
    if CanonicalZh not in [None, ''] and Auxiliary_HasChineseText(CanonicalZh):
        FinalName = CanonicalZh
    return SE, EP, RAWSE, RAWEP, FinalName, NameEN, NameRomaji, CanonicalID


def _ApplyAbsoluteEpisodeRemap(LocalBase, Chain):
    '''若 Chain 已拿到中文/英文剧名，尝试把长篇番的绝对集号映射到季内集号。'''
    if Chain is None:
        return Chain
    SE, EP, RAWSE, RAWEP, RAWName, NameEN, NameRomaji, CanonicalID = Chain
    from .episode_rules import Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode
    RemapTuple = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
        RAWSE, RAWEP, SE, EP, NameEN, NameRomaji, RAWName
    )
    if RemapTuple is None:
        return Chain
    NewRAWSE, NewRAWEP, NewSE, NewEP = RemapTuple
    return NewSE, NewEP, NewRAWSE, NewRAWEP, RAWName, NameEN, NameRomaji, CanonicalID


def Auxiliary_ResolveFileInfoWithFallback(File: str):
    '''对外主入口：AI 失败后的回退识别编排。

    返回 `(Info5, Meta)`：
    - `Info5` : 与 `Auxiliary_OpenAIIdentifyFileInfo` 一致的 5 元组；失败时 None
    - `Meta`  : dict {NameEN, NameRomaji, CanonicalID, CanonicalZh, Source}
    '''
    Local = Auxiliary_FallbackLocalRules(File)
    if Local is None:
        return None, None
    Chain = Auxiliary_FallbackTraditionalApis(Local)
    if Chain is None:
        # 传统三路也全失败时，至少用本地规则结果（含无中文名的 RAWName）尝试整理
        SE, EP, RAWSE, RAWEP, RAWName = Local
        Meta = {'NameEN': '', 'NameRomaji': '', 'CanonicalID': '', 'CanonicalZh': RAWName, 'Source': 'local_rules_only'}
        return (SE, EP, RAWSE, RAWEP, RAWName), Meta
    Chain = _ApplyAbsoluteEpisodeRemap(Local, Chain)
    SE, EP, RAWSE, RAWEP, RAWName, NameEN, NameRomaji, CanonicalID = Chain
    Meta = {
        'NameEN': NameEN,
        'NameRomaji': NameRomaji,
        'CanonicalID': CanonicalID,
        'CanonicalZh': RAWName,
        'Source': 'local_rules+traditional_api',
    }
    return (SE, EP, RAWSE, RAWEP, RAWName), Meta
