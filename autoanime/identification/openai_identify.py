"""
autoanime OpenAI 一次性全信息识别

对应原 `AutoAnimeMv.py`:
- `Auxiliary_NoteOpenAIIdentifyFailure`
- `Auxiliary_GetOpenAIIdentifyWarningLogPath`
- `Auxiliary_AppendOpenAIIdentifyWarningLog`
- `Auxiliary_OpenAIIdentifyFileInfo`

本模块直接调用 `apis.openai_client.Auxiliary_OpenAIChatCompletionsPost` 获得
多槽位轮换能力；剧名二次标准化使用 `identification.title_chain.Auxiliary_ResolvePlannedTitleChain`。
"""

import json

from os import path
from pathlib import Path as PathlibPath
from re import sub
from time import localtime, strftime, time

from .. import state
from ..apis.openai_client import Auxiliary_OpenAIChatCompletionsPost
from ..config_loader import (
    Auxiliary_GetOpenAIApiKey,
    Auxiliary_ParseBool,
    Auxiliary_ParseInt,
)
from ..logging_utils import Auxiliary_Log
from ..naming import Auxiliary_StripLeadingBracketReleaseTags
from .local_fallback import Auxiliary_IsFallbackEnabled


def _OpenAIFailLogLevel() -> str:
    '''在启用回退链路时，OpenAI 主路径的预失败信息降级为 INFO，避免与后续回退成功叠成“假 WARN”。'''
    return 'INFO' if Auxiliary_IsFallbackEnabled() else 'WARNING'
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
    Auxiliary_ParseJsonFromAIContent,
)


def Auxiliary_NoteOpenAIIdentifyFailure(reason, detail='', **extra):
    Pack = {'reason': str(reason), 'detail': str(detail)}
    for Key, Val in extra.items():
        Pack[Key] = Val
    state.LastOpenAIIdentifyFailure = Pack


def Auxiliary_GetOpenAIIdentifyWarningLogPath():
    if state.Runtime and getattr(state.Runtime, 'source_path', None):
        LogBasePath = state.Runtime.source_path
        if LogBasePath.exists() == False:
            LogBasePath = PathlibPath(state.PyPath)
    else:
        LogBasePath = PathlibPath(state.PyPath)
    OpDirName = str(state.OPERATION_LOG_DIR).strip() if state.OPERATION_LOG_DIR not in [None, ''] else 'logs'
    return LogBasePath / OpDirName / 'AutoAnime_openai_identify_warnings.json'


def Auxiliary_AppendOpenAIIdentifyWarningLog(entry: dict):
    '''追加 OpenAI 全信息识别失败记录到 logs/AutoAnime_openai_identify_warnings.json'''
    LogPath = Auxiliary_GetOpenAIIdentifyWarningLogPath()
    try:
        LogPath.parent.mkdir(parents=True, exist_ok=True)
        Records = []
        if LogPath.is_file():
            with open(LogPath, 'r', encoding='UTF-8') as LogFile:
                try:
                    OldPayload = json.load(LogFile)
                    if type(OldPayload) == dict and type(OldPayload.get('records')) == list:
                        Records = OldPayload['records']
                except Exception:
                    Records = []
        Row = dict(entry) if type(entry) == dict else {'detail': str(entry)}
        if 'timestamp' not in Row:
            Row['timestamp'] = strftime('%Y-%m-%d %H:%M:%S', localtime(time()))
        if 'run_id' not in Row:
            Row['run_id'] = state.CurrentRunID
        Records.append(Row)
        Records.sort(key=lambda r: (str(r.get('timestamp', '')), str(r.get('run_id', '')), str(r.get('input_basename', ''))))
        with open(LogPath, 'w', encoding='UTF-8') as LogFile:
            json.dump({'records': Records}, LogFile, ensure_ascii=False, indent=2)
    except Exception as err:
        Auxiliary_Log(f'OpenAI 识别告警日志写入失败: {err}', 'WARNING')


def Auxiliary_OpenAIIdentifyFileInfo(FileName):
    '''通过 OpenAI 一次性识别剧名/剧季/剧集；剧名经 TMDB 中文→Bangumi→TMDB 英文→OpenAI 译中文'''
    from .episode_rules import (
        Auxiliary_CoalesceEpisodeFromParsed,
        Auxiliary_CoalesceSeasonFromParsed,
        Auxiliary_NormalizeEpisodeToken,
        Auxiliary_PreDetectEpisodeHint,
    )
    from .title_chain import (
        Auxiliary_ApplyStandardTitleCacheToFileInfoRecord,
        Auxiliary_ResolvePlannedTitleChain,
    )
    from ..cache.canonical import Auxiliary_GetCanonicalTitleRecord

    state.LastOpenAIFileInfoMeta = {}
    state.LastOpenAIIdentifyFailure = None
    if state.USEOPENAIAPI != True or state.OPENAI_IDENTIFY_ALL != True:
        return None
    QueryFileName = path.basename(FileName)
    PromptBaseName = Auxiliary_StripLeadingBracketReleaseTags(QueryFileName)
    InvalidNameSet = {'', 'None', 'none', 'null', '未知', '无法识别', '无法判断', '不确定'}

    def BuildMetaFromRecord(CacheRecord):
        return {
            'NameEN': CacheRecord.get('NameEN', ''),
            'NameRomaji': CacheRecord.get('NameRomaji', ''),
            'CanonicalID': CacheRecord.get('CanonicalID', ''),
            'CanonicalZh': CacheRecord.get('RAWName', ''),
        }

    if QueryFileName in state.OpenAIIdentifyFileMemoryCache:
        CacheRecord = state.OpenAIIdentifyFileMemoryCache[QueryFileName]
        if type(CacheRecord) == dict and all([Key in CacheRecord for Key in ['SE', 'EP', 'RAWSE', 'RAWEP', 'RAWName']]):
            FixedRecord, Updated = Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(CacheRecord)
            if Updated == True:
                state.OpenAIIdentifyFileMemoryCache[QueryFileName] = FixedRecord
                CacheRecord = FixedRecord
            if CacheRecord.get('RAWName') in [None, '']:
                state.OpenAIIdentifyFileMemoryCache.pop(QueryFileName, None)
                CacheRecord = None
            if CacheRecord is not None:
                Auxiliary_Log(f'OpenAI文件识别内存缓存命中 << {CacheRecord}', 'INFO')
                state.LastOpenAIFileInfoMeta = BuildMetaFromRecord(CacheRecord)
                return CacheRecord['SE'], CacheRecord['EP'], CacheRecord['RAWSE'], CacheRecord['RAWEP'], CacheRecord['RAWName']

    ApiKey = Auxiliary_GetOpenAIApiKey()
    if ApiKey in [None, '']:
        Auxiliary_Log('OpenAI文件识别需要 OPENAI_API_KEY', _OpenAIFailLogLevel())
        Auxiliary_NoteOpenAIIdentifyFailure('missing_api_key', '未配置 OPENAI_API_KEY', input_basename=QueryFileName)
        return None

    RequestBody = {
        'messages': [
            {
                'role': 'system',
                'content': '你是番剧文件识别助手。请根据用户提供的单个文件名，识别并仅输出 JSON：{"anime_name_zh":"简体中文番剧名","anime_name_en":"英文名或常见英文写法","anime_name_romaji":"罗马音","season":"季数字(未知填1)","episode":"集数字或小数","special":false}。anime_name_zh 必须尽量返回简体中文标准名称；若当前无法确定中文，请保持 anime_name_zh 为空字符串，同时尽可能给出 anime_name_en 或 anime_name_romaji。anime_name_zh、anime_name_en、anime_name_romaji 只允许填写番剧主标题，禁止包含季信息（如 S2、Season 2、2nd Season、第二季等）。不要输出解释文本。'
                '文件名最前面的半角方括号 […] 与全角书名号式标签 【…】 中多为字幕组/发行方标记，不是番剧标题；anime_name_zh、anime_name_en、anime_name_romaji 只填作品主标题。'
            },
            {'role': 'user', 'content': PromptBaseName},
        ],
    }
    TemperatureValue = state.OPENAI_TEMPERATURE if state.OPENAI_TEMPERATURE not in [None, ''] else None
    if TemperatureValue is not None:
        try:
            RequestBody['temperature'] = float(TemperatureValue)
        except Exception:
            pass
    try:
        HttpData = Auxiliary_OpenAIChatCompletionsPost(RequestBody)
        if HttpData in [None, '']:
            Auxiliary_Log('OpenAI文件识别请求失败，未获得有效响应', _OpenAIFailLogLevel())
            Auxiliary_NoteOpenAIIdentifyFailure('no_http_response', '所有槽位请求失败或返回非成功状态', input_basename=QueryFileName)
            return None
        OpenAIData = HttpData.json()
        if type(OpenAIData) != dict:
            Auxiliary_Log('OpenAI文件识别返回数据结构异常', _OpenAIFailLogLevel())
            Auxiliary_NoteOpenAIIdentifyFailure('response_not_dict', '', input_basename=QueryFileName)
            return None
        Choices = OpenAIData.get('choices', [])
        if type(Choices) != list or Choices == []:
            Auxiliary_Log('OpenAI文件识别返回格式异常: 缺少 choices', _OpenAIFailLogLevel())
            Auxiliary_NoteOpenAIIdentifyFailure('no_choices', '', input_basename=QueryFileName)
            return None
        Message = Choices[0].get('message', {})
        ParsedData = Auxiliary_ParseJsonFromAIContent(Message.get('content', '') if type(Message) == dict else '')
        if type(ParsedData) != dict:
            Auxiliary_Log('OpenAI文件识别返回内容不是有效 JSON', _OpenAIFailLogLevel())
            RawPreview = Message.get('content', '') if type(Message) == dict else ''
            if type(RawPreview) == str and len(RawPreview) > 800:
                RawPreview = RawPreview[:800] + '…'
            Auxiliary_NoteOpenAIIdentifyFailure('content_not_json', 'choices[0].message.content 无法解析为对象', input_basename=QueryFileName, raw_content_preview=RawPreview)
            return None

        NameZH = Auxiliary_NormalizeApiTitle(
            ParsedData.get('anime_name_zh')
            or ParsedData.get('anime_name')
            or ParsedData.get('title')
            or ParsedData.get('name')
            or ''
        )
        NameEN = Auxiliary_NormalizeDisplayTitle(
            ParsedData.get('anime_name_en')
            or ParsedData.get('english_title')
            or ParsedData.get('title_en')
            or ParsedData.get('name_en')
            or ''
        )
        NameRomaji = Auxiliary_NormalizeDisplayTitle(
            ParsedData.get('anime_name_romaji')
            or ParsedData.get('romaji_title')
            or ParsedData.get('title_romaji')
            or ParsedData.get('name_romaji')
            or ''
        )
        if NameZH in InvalidNameSet:
            NameZH = ''
        if NameEN in InvalidNameSet:
            NameEN = ''
        if NameRomaji in InvalidNameSet:
            NameRomaji = ''
        if NameZH not in [None, ''] and Auxiliary_HasChineseText(NameZH) == False:
            NameZH = ''
        # ---- 防污染：拒绝 AI 返回的伪标题 ----
        _INVALID_TITLE_PATTERNS = (
            '空字符串', '无法对应', '并非已知', '可能是同人', '根据指令',
            '无法确定', '不是动画', '不是番剧', '无法识别为',
            '注经查询', '注：', '返回空', '请提供', '请输入',
            '这不是动画', '没有对应',
        )
        if NameZH not in [None, '']:
            for _pat in _INVALID_TITLE_PATTERNS:
                if _pat in NameZH:
                    Auxiliary_Log(f'OpenAI识别结果含非法模式「{_pat}」，已清空: {NameZH[:60]}', 'WARNING')
                    NameZH = ''
                    break
        if NameZH not in [None, ''] and len(NameZH) > 30:
            Auxiliary_Log(f'OpenAI识别结果过长({len(NameZH)}字符)，疑似解释性文本，已清空: {NameZH[:60]}…', 'WARNING')
            NameZH = ''
        if NameEN not in [None, '']:
            for _pat in _INVALID_TITLE_PATTERNS:
                if _pat in NameEN:
                    NameEN = ''
                    break
        if NameRomaji not in [None, '']:
            for _pat in _INVALID_TITLE_PATTERNS:
                if _pat in NameRomaji:
                    NameRomaji = ''
                    break
        AINameZH = NameZH

        RAWEP = Auxiliary_CoalesceEpisodeFromParsed(ParsedData)
        RAWEP, EpisodeSpecialFlag = Auxiliary_NormalizeEpisodeToken(RAWEP, QueryFileName)
        if RAWEP in [None, '']:
            Auxiliary_Log(f'OpenAI文件识别未返回可用剧集: {QueryFileName}', _OpenAIFailLogLevel())
            Snap = {}
            for Key in ('anime_name_zh', 'anime_name_en', 'anime_name_romaji', 'season', 'episode', 'ep', 'se', 'special'):
                if Key in ParsedData:
                    Snap[Key] = ParsedData.get(Key)
            Auxiliary_NoteOpenAIIdentifyFailure(
                'episode_missing',
                'episode/ep 缺失、为空或归一后不可用（注意：整数 0 是合法第 0 集）',
                input_basename=QueryFileName,
                openai_parsed_snapshot=Snap,
            )
            return None

        NameZH_out, CanonicalID, NameEN, NameRomaji = Auxiliary_ResolvePlannedTitleChain(AINameZH, NameEN, NameRomaji, FileName)
        RAWName = NameZH_out
        HintInfo = Auxiliary_PreDetectEpisodeHint(QueryFileName)
        if type(HintInfo) == dict:
            HintCanonicalID = str(HintInfo.get('CanonicalID') or '')
            if HintCanonicalID != '':
                HintRecord = Auxiliary_GetCanonicalTitleRecord(HintCanonicalID)
                if type(HintRecord) == dict:
                    HintZh = Auxiliary_NormalizeApiTitle(HintRecord.get('zh', ''))
                    if HintZh not in [None, '']:
                        RAWName = HintZh
                        CanonicalID = HintCanonicalID

        SpecialFlag = Auxiliary_ParseBool(ParsedData.get('special', False))
        if SpecialFlag != True:
            SpecialFlag = EpisodeSpecialFlag
        if SpecialFlag == True:
            SE = '00' if state.SEEPSINGLECHARACTER == False else '0'
            RAWSE = ''
        else:
            SeasonValue = Auxiliary_CoalesceSeasonFromParsed(ParsedData, '1')
            SeasonValue = sub(r'[^0-9]', '', str(SeasonValue).strip()) if SeasonValue not in [None, ''] else '1'
            SeasonValue = '1' if SeasonValue in [None, '', '0'] else SeasonValue
            RAWSE = SeasonValue
            SE = SeasonValue.zfill(2) if state.SEEPSINGLECHARACTER == False else SeasonValue.lstrip('0')
            if SE in [None, '']:
                SE = '1' if state.SEEPSINGLECHARACTER == True else '01'

        EP = '0' + RAWEP if (len(RAWEP) < 2 or ('.' in RAWEP and RAWEP[0] != '0')) and (state.SEEPSINGLECHARACTER == False) else RAWEP
        if state.SEEPSINGLECHARACTER == True:
            SE = SE.lstrip('0')
            EP = EP.lstrip('0')
            SE = SE if SE not in [None, ''] else '0'
            EP = EP if EP not in [None, ''] else '0'

        CacheRecord = {
            'SE': SE,
            'EP': EP,
            'RAWSE': RAWSE,
            'RAWEP': RAWEP,
            'RAWName': RAWName,
            'NameEN': NameEN,
            'NameRomaji': NameRomaji,
            'CanonicalID': CanonicalID if CanonicalID not in [None, ''] else '',
        }
        CacheRecord, _ = Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(CacheRecord)
        SE = CacheRecord.get('SE', SE)
        EP = CacheRecord.get('EP', EP)
        RAWSE = CacheRecord.get('RAWSE', RAWSE)
        RAWEP = CacheRecord.get('RAWEP', RAWEP)
        RAWName = CacheRecord.get('RAWName', RAWName)
        state.OpenAIIdentifyFileMemoryCache[QueryFileName] = CacheRecord
        state.LastOpenAIFileInfoMeta = BuildMetaFromRecord(CacheRecord)
        Auxiliary_Log(f'OpenAI文件识别成功 => 剧名:{RAWName} 季:{SE} 集:{EP}', 'INFO')
        return SE, EP, RAWSE, RAWEP, RAWName
    except Exception as err:
        Auxiliary_Log(f'OpenAI文件识别处理失败: {err}', _OpenAIFailLogLevel())
        Auxiliary_NoteOpenAIIdentifyFailure('exception', str(err), input_basename=path.basename(FileName))
        return None
