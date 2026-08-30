"""
autoanime OpenAI 兼容接口客户端

对应原 `AutoAnimeMv.py`:
- `Auxiliary_GetOpenAIRuntimeStatePath`
- `Auxiliary_LoadOpenAIRuntimeState`
- `Auxiliary_SaveOpenAIRuntimeState`
- `Auxiliary_GetOpenAIEndpointSlots`
- `Auxiliary_ParseOpenAIRotateStatusCodes`
- `Auxiliary_OpenAIHttpBodyIndicatesQuota`
- `Auxiliary_OpenAIChatCompletionsPost`
- `Auxiliary_OpenAITranslateForeignTitleToChinese`
"""

import json

from pathlib import Path as PathlibPath
from time import time

from requests import Session, exceptions

_OpenAISession = Session()
_OpenAISession.trust_env = False

from .. import state
from ..config_loader import (
    Auxiliary_GetCacheStorePath,
    Auxiliary_GetOpenAIApiKey,
    Auxiliary_ParseDelimitedConfigList,
    Auxiliary_ParseInt,
)
from ..logging_utils import Auxiliary_Log
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
    Auxiliary_ParseJsonFromAIContent,
)


def Auxiliary_GetOpenAIRuntimeStatePath() -> PathlibPath:
    return Auxiliary_GetCacheStorePath().parent / 'openai_runtime_state.json'


def Auxiliary_LoadOpenAIRuntimeState():
    StatePath = Auxiliary_GetOpenAIRuntimeStatePath()
    if StatePath.is_file() == False:
        return {'active_slot_index': 0, 'updated_at': 0.0}
    try:
        with open(StatePath, 'r', encoding='UTF-8') as StateFile:
            Data = json.load(StateFile)
        if type(Data) != dict:
            return {'active_slot_index': 0, 'updated_at': 0.0}
        IndexValue = Auxiliary_ParseInt(Data.get('active_slot_index', 0), 0)
        if IndexValue < 0:
            IndexValue = 0
        return {'active_slot_index': IndexValue, 'updated_at': float(Data.get('updated_at', 0.0) or 0.0)}
    except Exception:
        return {'active_slot_index': 0, 'updated_at': 0.0}


def Auxiliary_SaveOpenAIRuntimeState(StateDict):
    StatePath = Auxiliary_GetOpenAIRuntimeStatePath()
    try:
        StatePath.parent.mkdir(parents=True, exist_ok=True)
        Payload = {
            'active_slot_index': int(StateDict.get('active_slot_index', 0)),
            'updated_at': time(),
        }
        with open(StatePath, 'w', encoding='UTF-8') as StateFile:
            json.dump(Payload, StateFile, ensure_ascii=False, indent=2)
    except Exception as err:
        Auxiliary_Log(f'OpenAI 运行时状态写入失败: {err}', 'WARNING')


def Auxiliary_GetOpenAIEndpointSlots():
    UrlList = Auxiliary_ParseDelimitedConfigList(state.OPENAI_BASE_URLS)
    if UrlList == []:
        BaseFallback = state.OPENAI_BASE_URL if state.OPENAI_BASE_URL not in [None, ''] else ''
        UrlList = [str(BaseFallback).strip()] if str(BaseFallback).strip() not in [None, ''] else []
    KeyList = Auxiliary_ParseDelimitedConfigList(state.OPENAI_API_KEYS)
    if KeyList == []:
        SingleKey = Auxiliary_GetOpenAIApiKey()
        KeyList = [SingleKey] if SingleKey not in [None, ''] else []
    if UrlList == [] or KeyList == []:
        return []
    ModelList = Auxiliary_ParseDelimitedConfigList(state.OPENAI_MODELS)
    if ModelList == []:
        SingleModel = state.OPENAI_MODEL if state.OPENAI_MODEL not in [None, ''] else ''
        ModelList = [SingleModel] if SingleModel not in [None, ''] else []
    if ModelList == []:
        return []
    SlotCount = max(len(UrlList), len(KeyList), len(ModelList))
    Slots = []
    for SlotIndex in range(SlotCount):
        UrlItem = UrlList[SlotIndex % len(UrlList)].rstrip('/')
        KeyItem = KeyList[SlotIndex % len(KeyList)]
        ModelItem = ModelList[SlotIndex % len(ModelList)]
        Slots.append((UrlItem, KeyItem, ModelItem))
    return Slots


def Auxiliary_ParseOpenAIRotateStatusCodes():
    RawText = str(state.OPENAI_KEY_ROTATE_ON_STATUS).strip() if state.OPENAI_KEY_ROTATE_ON_STATUS not in [None, ''] else '401,429'
    Codes = set()
    for Part in RawText.replace('|', ',').split(','):
        Part = Part.strip()
        if Part.isdigit():
            Codes.add(int(Part))
    if Codes == set():
        Codes = {401, 429}
    return Codes


def Auxiliary_OpenAIHttpBodyIndicatesQuota(ResponseText):
    if ResponseText in [None, '']:
        return False
    LowerText = str(ResponseText).lower()
    return 'insufficient_quota' in LowerText or 'rate_limit' in LowerText or 'billing' in LowerText


def Auxiliary_OpenAIChatCompletionsPost(RequestJson):
    Slots = Auxiliary_GetOpenAIEndpointSlots()
    if Slots == []:
        return None
    StateSnapshot = Auxiliary_LoadOpenAIRuntimeState()
    StartIndex = Auxiliary_ParseInt(StateSnapshot.get('active_slot_index', 0), 0) % len(Slots)
    TimeoutSeconds = Auxiliary_ParseInt(state.OPENAI_TIMEOUT_SECONDS, 60)
    if TimeoutSeconds <= 0:
        TimeoutSeconds = 60
    RetryTimes = Auxiliary_ParseInt(state.NETERRRECTRYTIMS, 2)
    if RetryTimes < 0:
        RetryTimes = 0
    RotateCodes = Auxiliary_ParseOpenAIRotateStatusCodes()
    MaxConsecutive = Auxiliary_ParseInt(state.OPENAI_KEY_MAX_CONSECUTIVE_FAILURES, 3)
    if MaxConsecutive <= 0:
        MaxConsecutive = 1

    for SlotOffset in range(len(Slots)):
        SlotIndex = (StartIndex + SlotOffset) % len(Slots)
        BaseUrl, ApiKey, SlotModel = Slots[SlotIndex]
        if ApiKey in [None, '']:
            continue
        SlotRequestJson = dict(RequestJson)
        SlotRequestJson['model'] = SlotModel
        ConsecutiveFailures = 0
        for RetryIndex in range(RetryTimes + 1):
            HttpData = None
            try:
                HttpData = _OpenAISession.post(
                    f'{BaseUrl.rstrip("/")}/v1/chat/completions',
                    json=SlotRequestJson,
                    headers={
                        'Authorization': f'Bearer {ApiKey}',
                        'Content-Type': 'application/json',
                        'User-Agent': f'AutoAnimeMv/{state.Versions}',
                    },
                    timeout=TimeoutSeconds,
                )
            except exceptions.RequestException as err:
                ConsecutiveFailures += 1
                if RetryIndex < RetryTimes:
                    Auxiliary_Log(f'OpenAI 请求异常，槽位 {SlotIndex+1}/{len(Slots)} 第{RetryIndex+1}/{RetryTimes+1}次重试: {err}', 'WARNING')
                    continue
                Auxiliary_Log(f'OpenAI 请求失败，槽位 {SlotIndex+1}/{len(Slots)}: {err}', 'WARNING')
                break
            if HttpData.status_code == 200:
                StateSnapshot['active_slot_index'] = SlotIndex
                Auxiliary_SaveOpenAIRuntimeState(StateSnapshot)
                return HttpData
            ResponseText = ''
            try:
                ResponseText = HttpData.text
            except Exception:
                ResponseText = ''
            if HttpData.status_code in RotateCodes or Auxiliary_OpenAIHttpBodyIndicatesQuota(ResponseText):
                Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 返回 {HttpData.status_code}，切换下一槽位', 'WARNING')
                break
            ConsecutiveFailures += 1
            if RetryIndex < RetryTimes:
                Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 状态码 {HttpData.status_code}，重试 {RetryIndex+1}/{RetryTimes+1}', 'WARNING')
                continue
            Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 状态码 {HttpData.status_code}，放弃本槽位', 'WARNING')
            break
        if ConsecutiveFailures >= MaxConsecutive:
            Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 连续失败达 {MaxConsecutive}，尝试下一槽位', 'WARNING')
    return None


def Auxiliary_OpenAITranslateForeignTitleToChinese(ForeignTitle):
    '''将外文剧名译为简体中文（剧名链最后一步）'''
    ForeignTitle = Auxiliary_NormalizeDisplayTitle(ForeignTitle)
    if ForeignTitle in [None, '']:
        return None
    if state.USEOPENAIAPI != True:
        return None
    ApiKey = Auxiliary_GetOpenAIApiKey()
    if ApiKey in [None, '']:
        Auxiliary_Log('OpenAI 译名需要密钥', 'WARNING')
        return None
    Slots = Auxiliary_GetOpenAIEndpointSlots()
    if Slots == []:
        Auxiliary_Log('OpenAI 译名需要配置有效的网关地址/模型/密钥', 'WARNING')
        return None
    StateSnapshot = Auxiliary_LoadOpenAIRuntimeState()
    StartIndex = Auxiliary_ParseInt(StateSnapshot.get('active_slot_index', 0), 0) % len(Slots)
    RotateCodes = Auxiliary_ParseOpenAIRotateStatusCodes()
    TimeoutSeconds = Auxiliary_ParseInt(state.OPENAI_TIMEOUT_SECONDS, 60)
    if TimeoutSeconds <= 0:
        TimeoutSeconds = 60
    try:
        TemperatureValue = state.OPENAI_TEMPERATURE if state.OPENAI_TEMPERATURE not in [None, ''] else None
        TemperatureValue = float(TemperatureValue) if TemperatureValue is not None else None
    except Exception:
        TemperatureValue = None
    for SlotOffset in range(len(Slots)):
        SlotIndex = (StartIndex + SlotOffset) % len(Slots)
        BaseUrl, ApiKey, SlotModel = Slots[SlotIndex]
        RequestBody = {
            'model': SlotModel,
            'messages': [
                {'role': 'system', 'content': '你是番剧译名助手。输入为一部动画的日文/英文或罗马音标题，请只输出一个最常用的简体中文官方译名，不要季数、集数、引号或解释。无法确定则只输出空字符串。'},
                {'role': 'user', 'content': ForeignTitle},
            ],
        }
        if TemperatureValue is not None:
            RequestBody['temperature'] = TemperatureValue
        try:
            HttpData = _OpenAISession.post(
                f'{BaseUrl.rstrip("/")}/v1/chat/completions',
                json=RequestBody,
                headers={
                    'Authorization': f'Bearer {ApiKey}',
                    'Content-Type': 'application/json',
                    'User-Agent': f'AutoAnimeMv/{state.Versions}',
                },
                timeout=TimeoutSeconds,
            )
        except exceptions.RequestException as err:
            Auxiliary_Log(f'OpenAI 译名请求异常，槽位 {SlotIndex+1}/{len(Slots)}: {err}', 'WARNING')
            continue
        if HttpData.status_code != 200:
            if HttpData.status_code in RotateCodes:
                Auxiliary_Log(f'OpenAI 译名槽位 {SlotIndex+1}/{len(Slots)} 返回 {HttpData.status_code}，切换下一槽位', 'WARNING')
                continue
            Auxiliary_Log(f'OpenAI 译名请求失败,状态码 {HttpData.status_code}，槽位 {SlotIndex+1}/{len(Slots)}', 'WARNING')
            continue
        StateSnapshot['active_slot_index'] = SlotIndex
        Auxiliary_SaveOpenAIRuntimeState(StateSnapshot)
        try:
            OpenAIData = HttpData.json()
            if type(OpenAIData) != dict:
                continue
            Choices = OpenAIData.get('choices', [])
            if type(Choices) != list or Choices == []:
                continue
            Message = Choices[0].get('message', {})
            RawText = Message.get('content', '') if type(Message) == dict else ''
            Parsed = Auxiliary_ParseJsonFromAIContent(RawText)
            if type(Parsed) == dict:
                ApiTitle = Auxiliary_NormalizeApiTitle(
                    Parsed.get('anime_name_zh') or Parsed.get('anime_name') or Parsed.get('title') or ''
                )
            else:
                ApiTitle = Auxiliary_NormalizeApiTitle(RawText)
            if ApiTitle in ['', 'None', 'none', 'null', '未知', '无法识别', '无法判断', '不确定']:
                continue
            if Auxiliary_HasChineseText(ApiTitle) != True:
                continue
            # ---- 防污染：拒绝过长或含解释性文本的伪标题 ----
            _INVALID_TITLE_PATTERNS = (
                '空字符串', '无法对应', '并非已知', '可能是同人', '根据指令',
                '无法确定', '不是动画', '不是番剧', '无法识别为',
                '注经查询', '注：', '返回空', '请提供', '请输入',
            )
            if len(ApiTitle) > 30:
                Auxiliary_Log(f'OpenAI 译名结果过长({len(ApiTitle)}字符)，疑似解释性文本，已拒绝: {ApiTitle[:60]}…', 'WARNING')
                continue
            for _pat in _INVALID_TITLE_PATTERNS:
                if _pat in ApiTitle:
                    Auxiliary_Log(f'OpenAI 译名结果含非法模式「{_pat}」，已拒绝: {ApiTitle[:60]}', 'WARNING')
                    break
            else:
                return ApiTitle
            continue
        except Exception as err:
            Auxiliary_Log(f'OpenAI 译名解析失败（槽位 {SlotIndex+1}/{len(Slots)}）: {err}', 'WARNING')
            continue
