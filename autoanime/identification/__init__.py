"""
autoanime 剧集/剧名/剧季识别子包

- `openai_identify` : OpenAI 一次性全信息识别（主路径）
- `title_chain`     : 剧名标准化链 TMDB->Bangumi->TMDB EN->OpenAI 译名
- `episode_rules`   : 季/集规则、Jujutsu 特例、预检索
- 本模块另外导出 `Processing_Identification`，保持旧流水线兼容。
"""

from .. import state
from ..logging_utils import Auxiliary_Exit, Auxiliary_Log
from ..naming import (
    Auxiliary_AnimeFileCheck,
    Auxiliary_RMOTSTR,
    Auxiliary_RMSubtitlingTeam,
    Auxiliary_UniformOTSTR,
)
from .local_fallback import (
    Auxiliary_IsFallbackEnabled,
    Auxiliary_NoteOpenAIBreakerEvent,
    Auxiliary_ResetOpenAIBreaker,
    Auxiliary_ResolveFileInfoWithFallback,
    Auxiliary_ShouldTripOpenAIBreaker,
)
from .openai_identify import (
    Auxiliary_AppendOpenAIIdentifyWarningLog,
    Auxiliary_GetOpenAIIdentifyWarningLogPath,
    Auxiliary_NoteOpenAIIdentifyFailure,
    Auxiliary_OpenAIIdentifyFileInfo,
)
from .title_chain import Auxiliary_ResolvePlannedTitleChain


def Processing_Identification(File: str):
    '''OpenAI 一次性识别为主路径，失败时按 `OPENAI_FALLBACK_ON_FAILURE` 开关走本地 + 传统 API 回退。

    返回：识别成功则为 (SE, EP, RAWSE, RAWEP, RAWName)；彻底失败则为 None。
    同时写入 state.LastOpenAIFileInfoMeta / state.LastIdentificationFromAI，供上层流水线读取。
    '''
    state.LastIdentificationFromAI = False
    state.LastOpenAIFileInfoMeta = {}

    NewFile = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(File)))
    AnimeFileCheckFlag = Auxiliary_AnimeFileCheck(NewFile)
    if AnimeFileCheckFlag != True:
        Auxiliary_Log(f'当前文件属于{AnimeFileCheckFlag},跳过处理', 'INFO')
        return None
    Auxiliary_Log('-' * 80, 'INFO')

    FallbackEnabled = Auxiliary_IsFallbackEnabled()
    BreakerTripped = Auxiliary_ShouldTripOpenAIBreaker()
    SkipOpenAI = (
        state.USEOPENAIAPI != True
        or state.OPENAI_IDENTIFY_ALL != True
        or (FallbackEnabled and BreakerTripped)
    )
    if SkipOpenAI and FallbackEnabled != True and (state.USEOPENAIAPI != True or state.OPENAI_IDENTIFY_ALL != True):
        Auxiliary_Exit('必须启用 USEOPENAIAPI 与 OPENAI_IDENTIFY_ALL，或启用 OPENAI_FALLBACK_ON_FAILURE 走回退链路')

    state.LastOpenAIIdentifyFailure = None
    OpenAIIdentifyData = None
    if SkipOpenAI != True:
        OpenAIIdentifyData = Auxiliary_OpenAIIdentifyFileInfo(File)

    if OpenAIIdentifyData is not None:
        state.LastIdentificationFromAI = True
        return OpenAIIdentifyData

    if SkipOpenAI != True and type(state.LastOpenAIIdentifyFailure) == dict:
        Auxiliary_NoteOpenAIBreakerEvent(state.LastOpenAIIdentifyFailure)

    if FallbackEnabled != True:
        BaseRow = {
            'input_basename': File,
            'stage': 'Processing_Identification',
        }
        if type(state.LastOpenAIIdentifyFailure) == dict:
            BaseRow.update(state.LastOpenAIIdentifyFailure)
        else:
            BaseRow['reason'] = 'openai_identify_returned_none'
            BaseRow['detail'] = 'Auxiliary_OpenAIIdentifyFileInfo 返回 None（可能为 mock 或未记录原因）'
        Auxiliary_AppendOpenAIIdentifyWarningLog(BaseRow)
        Auxiliary_Log(
            f'OpenAI 全信息识别失败，已跳过文件: {File}（明细已追加至 {Auxiliary_GetOpenAIIdentifyWarningLogPath().name}）',
            'ERROR',
        )
        return None

    # 走回退链路
    Info5, Meta = Auxiliary_ResolveFileInfoWithFallback(File)
    if Info5 is None:
        BaseRow = {
            'input_basename': File,
            'stage': 'Processing_Identification',
            'fallback': 'exhausted',
        }
        if type(state.LastOpenAIIdentifyFailure) == dict:
            BaseRow.update(state.LastOpenAIIdentifyFailure)
        Auxiliary_AppendOpenAIIdentifyWarningLog(BaseRow)
        Auxiliary_Log(
            f'OpenAI 识别 + 本地回退 + 传统 API 全部失败，已跳过文件: {File}',
            'ERROR',
        )
        return None

    if type(Meta) == dict:
        state.LastOpenAIFileInfoMeta = {
            'NameEN': Meta.get('NameEN', ''),
            'NameRomaji': Meta.get('NameRomaji', ''),
            'CanonicalID': Meta.get('CanonicalID', ''),
            'CanonicalZh': Meta.get('CanonicalZh', ''),
        }
    state.LastIdentificationFromAI = False
    if BreakerTripped:
        Auxiliary_Log(
            f'OpenAI 熔断已触发（连续 401/403/429/missing_api_key >= 阈值），当前文件直接走回退链路: {File}',
            'INFO',
        )
    Auxiliary_Log(
        f'openai_failed_fallback_success << File={File}, Source={Meta.get("Source") if type(Meta) == dict else ""}',
        'INFO',
    )
    return Info5


__all__ = [
    'Processing_Identification',
    'Auxiliary_OpenAIIdentifyFileInfo',
    'Auxiliary_ResolvePlannedTitleChain',
    'Auxiliary_NoteOpenAIIdentifyFailure',
    'Auxiliary_AppendOpenAIIdentifyWarningLog',
    'Auxiliary_GetOpenAIIdentifyWarningLogPath',
    'Auxiliary_IsFallbackEnabled',
    'Auxiliary_ResolveFileInfoWithFallback',
    'Auxiliary_NoteOpenAIBreakerEvent',
    'Auxiliary_ShouldTripOpenAIBreaker',
    'Auxiliary_ResetOpenAIBreaker',
]
