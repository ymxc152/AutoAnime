"""
autoanime Processing_Main（主流水线）

对应原 `AutoAnimeMv.py::Processing_Main`，关键增强（fix_show_index todo）：
- `ShowOrganizationIndex` 跳过前需校验 `expected_dst` 是否仍然存在：
    - 目标存在 + 与源同物理文件 => 照旧跳过（记 `already_organized_show_cache`）；
    - 目标缺失 => 自愈剔除 tag + 正常整理（记 `already_organized_show_cache_stale`）；
    - 有 tag 但缺少 `expected_dst`（老数据）=> 退化为旧行为跳过。
"""

from os import path

from .. import state
from ..cache.canonical import (
    Auxiliary_GetCanonicalTitleRecord,
    Auxiliary_ResolveCanonicalTitleByAliases,
    Auxiliary_UpsertCanonicalTitle,
)
from ..cache.show_index import (
    Auxiliary_ShowClearOrganizedEpisode,
    Auxiliary_ShowHasOrganizedEpisode,
    Auxiliary_ShowMarkOrganizedEpisode,
    Auxiliary_FormatOrganizedEpisodeTag,
    Auxiliary_ShowFindCrossCanonicalEpisode,
    Auxiliary_ShowClearDuplicateDstPath,
)
from ..identification import Processing_Identification
from ..identification.episode_rules import (
    Auxiliary_BuildEpisodeDecisionKey,
    Auxiliary_GetAbsoluteSourcePath,
    Auxiliary_GetSourceFileMTime,
    Auxiliary_PreDetectEpisodeHint,
)
from ..identification.title_chain import Auxiliary_ShouldCacheResolvedFileInfo
from ..logging_utils import Auxiliary_Log
from ..naming import Auxiliary_FileType
from ..scanning import Auxiliary_IsIncompleteDownloadFile
from ..sorting import Sorting_Mv
from ..sorting.file_ops import Auxiliary_IsSamePhysicalFile
from ..sorting.subtitles import Auxiliary_IDEASS
from ..text_utils import Auxiliary_HasChineseText, Auxiliary_NormalizeAliasKey
from .operation_log import Auxiliary_RecordOperation


def _CurrentAliasesMatchCanonicalID(CanonicalID, RAWName, ApiName, NameEN='', NameRomaji=''):
    '''校验当前文件的剧名/别名与已有 CanonicalID 是否属于同一番剧。

    用于「剧名漂移保护」复用 CrossCID 前的安全闸门，避免无关番剧因相同 EP 被错误合并。
    校验策略：
    1. 当前别名已直接解析到该 CanonicalID（alias index 命中）；
    2. 与 CanonicalID 记录的 zh/en/romaji 归一化后精确匹配；
    3. 子串相似兜底（至少一方长度 >= 4），允许同一番剧的不同拼写/缩写。
    '''
    if CanonicalID in [None, '']:
        return False

    # 1) alias index 直接命中
    ResolvedZh, ResolvedCID, _ = Auxiliary_ResolveCanonicalTitleByAliases(
        RAWName, ApiName, NameEN, NameRomaji
    )
    if ResolvedCID == CanonicalID:
        return True

    # 2) 与 canonical 记录的字段做归一化比较
    Record = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
    if type(Record) != dict:
        return False

    CurrentTitles = [RAWName, ApiName, NameEN, NameRomaji]
    CurrentKeys = {Auxiliary_NormalizeAliasKey(T) for T in CurrentTitles if T not in [None, '']}
    CurrentKeys.discard('')
    CrossKeys = {
        Auxiliary_NormalizeAliasKey(Record.get('zh', '')),
        Auxiliary_NormalizeAliasKey(Record.get('en', '')),
        Auxiliary_NormalizeAliasKey(Record.get('romaji', '')),
    }
    CrossKeys.discard('')

    if not CurrentKeys or not CrossKeys:
        return False

    # 精确匹配
    if CurrentKeys & CrossKeys:
        return True

    # 子串相似兜底
    for CK in CurrentKeys:
        for XK in CrossKeys:
            if CK in XK or XK in CK:
                if len(CK) >= 4 or len(XK) >= 4:
                    return True

    return False


def Processing_Main(LorT):
    '''遍历识别 + 调度整理。'''
    SubtitleFiles = []
    if type(LorT) == tuple:
        VideoFiles = LorT[0]
        SubtitleFiles = LorT[1]
    else:
        VideoFiles = LorT

    if type(VideoFiles) != list:
        return

    VideoFiles = sorted(VideoFiles, key=lambda X: Auxiliary_GetSourceFileMTime(X))
    for SourceFile in VideoFiles:
        File = path.basename(SourceFile)
        SourceAbsPath = Auxiliary_GetAbsoluteSourcePath(SourceFile)
        SourceMTime = Auxiliary_GetSourceFileMTime(SourceFile)
        state.LastOpenAIFileInfoMeta = {}
        if Auxiliary_IsIncompleteDownloadFile(File):
            Auxiliary_Log(f'跳过未完成下载文件: {SourceFile}', 'INFO')
            continue
        if Auxiliary_FileType(File) == 'ASS':
            Auxiliary_Log(f'跳过仅字幕文件主处理: {SourceFile}', 'INFO')
            continue

        PreDetectHint = Auxiliary_PreDetectEpisodeHint(File)
        if type(PreDetectHint) == dict and PreDetectHint.get('EpisodeKey') in state.EpisodeDecisionDataCache:
            ExistingDecision = state.EpisodeDecisionDataCache[PreDetectHint.get('EpisodeKey')]
            ExistingMTime = float(ExistingDecision.get('source_mtime', 0.0))
            if SourceMTime >= ExistingMTime:
                ExistingDst = ExistingDecision.get('dst', '')
                Auxiliary_Log(f'同集已保留更早文件，跳过较新重复资源: {SourceFile}', 'INFO')
                Auxiliary_RecordOperation('skip', SourceAbsPath, ExistingDst, 'skipped', 'newer_duplicate_kept_oldest')
                continue

        flag = Processing_Identification(File)
        if flag is None:
            continue
        SE, EP, RAWSE, RAWEP, RAWName = flag
        NameEN = state.LastOpenAIFileInfoMeta.get('NameEN', '')
        NameRomaji = state.LastOpenAIFileInfoMeta.get('NameRomaji', '')
        CanonicalID = state.LastOpenAIFileInfoMeta.get('CanonicalID', '')
        HintCanonicalID = PreDetectHint.get('CanonicalID', '') if type(PreDetectHint) == dict else ''
        HintApiName = PreDetectHint.get('ApiName', '') if type(PreDetectHint) == dict else ''

        if state.animename not in ['', None]:
            ApiName = state.animename
            Auxiliary_Log('当前文件已由 OpenAI 识别季集，剧名使用手动指定 animename', 'INFO')
        else:
            ApiName = state.LastOpenAIFileInfoMeta.get('CanonicalZh') or RAWName
            if HintCanonicalID not in [None, ''] and CanonicalID not in [None, ''] and HintCanonicalID != CanonicalID:
                Auxiliary_Log(f'检测到单集剧名漂移，采用历史别名映射纠偏: {File}', 'WARNING')
                CanonicalID = HintCanonicalID
                if HintApiName not in [None, '']:
                    ApiName = HintApiName
                    RAWName = HintApiName
            elif HintCanonicalID not in [None, ''] and CanonicalID in [None, '']:
                CanonicalID = HintCanonicalID
                if HintApiName not in [None, ''] and Auxiliary_HasChineseText(str(ApiName)) == False:
                    ApiName = HintApiName
            if Auxiliary_HasChineseText(str(ApiName)) == False:
                Auxiliary_Log('剧名未收敛到中文', 'WARNING')
            else:
                Auxiliary_Log('OpenAI 识别与剧名链已完成', 'INFO')
        if NameEN in [None, ''] and Auxiliary_HasChineseText(RAWName) == False:
            NameEN = RAWName
        CanonicalSourceTag = 'openai_identify' if state.LastIdentificationFromAI else 'local_fallback'
        CanonicalFromMainID, CanonicalFromMainZh = Auxiliary_UpsertCanonicalTitle(
            ApiName, NameEN, NameRomaji, CanonicalSourceTag, [RAWName, ApiName, File]
        )
        if CanonicalFromMainZh not in [None, '']:
            ApiName = CanonicalFromMainZh
        if CanonicalID in [None, ''] and CanonicalFromMainID not in [None, '']:
            CanonicalID = CanonicalFromMainID

        # fix_show_index：ShowOrganizationIndex 盲跳自愈
        if CanonicalID not in [None, '']:
            HasTag, ExpectedDst = Auxiliary_ShowHasOrganizedEpisode(CanonicalID, SE, EP)

            # 方案1：剧名漂移保护 — 若当前CanonicalID无记录，但其他CanonicalID已有同SE/EP的有效目标
            if not HasTag:
                CrossCID, CrossDst = Auxiliary_ShowFindCrossCanonicalEpisode(SE, EP)
                if CrossCID and _CurrentAliasesMatchCanonicalID(CrossCID, RAWName, ApiName, NameEN, NameRomaji):
                    CanonicalID = CrossCID
                    HasTag = True
                    ExpectedDst = CrossDst
                    Auxiliary_Log(f'剧名漂移保护：复用已有 CanonicalID={CrossCID} (SE={SE}, EP={EP})', 'INFO')
                elif CrossCID:
                    Auxiliary_Log(
                        f'剧名漂移保护拒绝：{RAWName} 与 CanonicalID={CrossCID} 剧名不匹配 (SE={SE}, EP={EP})',
                        'WARNING',
                    )

            if HasTag == True:
                TagLabel = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
                ShouldSkip = False
                StaleReason = ''
                if ExpectedDst is not None and ExpectedDst.exists():
                    # 目标仍在：若与源是同一物理文件，跳过整理；否则仍跳过但降级日志
                    ShouldSkip = True
                    if Auxiliary_IsSamePhysicalFile(SourceAbsPath, ExpectedDst):
                        StaleReason = 'already_organized_show_cache'
                    else:
                        StaleReason = 'already_organized_show_cache'
                elif ExpectedDst is None:
                    # 老数据（未记录 expected_dst）：维持旧行为，跳过
                    ShouldSkip = True
                    StaleReason = 'already_organized_show_cache'
                else:
                    # 有 tag 但目标缺失：自愈，剔除 tag 后照常整理
                    ShouldSkip = False
                    StaleReason = 'already_organized_show_cache_stale'
                    Changed = Auxiliary_ShowClearOrganizedEpisode(CanonicalID, SE, EP)
                    if Changed:
                        Auxiliary_Log(
                            f'ShowIndex 自愈：目标缺失已剔除 tag {TagLabel} << {ApiName}',
                            'INFO',
                        )
                if ShouldSkip:
                    Auxiliary_Log(
                        f'跳过已整理剧集（ShowOrganizationIndex）: {TagLabel} << {ApiName}',
                        'INFO',
                    )
                    Auxiliary_RecordOperation('skip', SourceAbsPath, str(ExpectedDst or ''), 'skipped', StaleReason)
                    continue
                else:
                    Auxiliary_RecordOperation(
                        'skip', SourceAbsPath, '', 'recover', StaleReason,
                    )

        EpisodeKey = Auxiliary_BuildEpisodeDecisionKey(ApiName, SE, EP, File)
        if EpisodeKey not in [None, ''] and EpisodeKey in state.EpisodeDecisionDataCache:
            ExistingDecision = state.EpisodeDecisionDataCache[EpisodeKey]
            ExistingMTime = float(ExistingDecision.get('source_mtime', 0.0))
            if SourceMTime >= ExistingMTime:
                ExistingDst = ExistingDecision.get('dst', '')
                Auxiliary_Log(f'同集已保留更早文件，跳过较新重复资源: {SourceFile}', 'INFO')
                Auxiliary_RecordOperation('skip', SourceAbsPath, ExistingDst, 'skipped', 'newer_duplicate_kept_oldest')
                continue

        ASSList = Auxiliary_IDEASS(RAWName, RAWSE, RAWEP, SubtitleFiles) if SubtitleFiles != [] else None
        MainOperationResult = Sorting_Mv(File, RAWName, SE, EP, ASSList, ApiName, SourceFilePath=SourceFile)
        DstPath = MainOperationResult.get('dst', '') if type(MainOperationResult) == dict else ''

        if Auxiliary_ShouldCacheResolvedFileInfo(MainOperationResult) and CanonicalID not in [None, '']:
            # 方案2：清理其他 CanonicalID 下指向同一目标路径的旧记录，避免 cache_stale 反复触发
            if DstPath:
                Cleared = Auxiliary_ShowClearDuplicateDstPath(DstPath, ExcludeCanonicalID=CanonicalID, ExcludeTag=Auxiliary_FormatOrganizedEpisodeTag(SE, EP))
                if Cleared:
                    Auxiliary_Log(f'ShowIndex 去重：已清理其他 CanonicalID 指向同一目标路径 {DstPath} 的旧记录', 'INFO')
            Auxiliary_ShowMarkOrganizedEpisode(CanonicalID, ApiName, NameEN, NameRomaji, SE, EP, DstPath=DstPath)

        if EpisodeKey not in [None, '']:
            ExistingDecision = state.EpisodeDecisionDataCache.get(EpisodeKey, {})
            ExistingMTime = float(ExistingDecision.get('source_mtime', 0.0)) if type(ExistingDecision) == dict else 0.0
            if type(ExistingDecision) != dict or ExistingDecision == {} or SourceMTime <= ExistingMTime:
                state.EpisodeDecisionDataCache[EpisodeKey] = {
                    'source_mtime': SourceMTime,
                    'src': str(SourceAbsPath),
                    'dst': DstPath,
                    'resolved': {
                        'SE': str(SE),
                        'EP': str(EP),
                        'RAWSE': str(RAWSE),
                        'RAWEP': str(RAWEP),
                        'RAWName': str(RAWName),
                        'ApiName': str(ApiName),
                        'NameEN': str(NameEN) if NameEN not in [None, ''] else '',
                        'NameRomaji': str(NameRomaji) if NameRomaji not in [None, ''] else '',
                        'CanonicalID': str(CanonicalID) if CanonicalID not in [None, ''] else '',
                    },
                }
