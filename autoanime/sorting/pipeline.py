"""
autoanime Sorting_Mv（整理主函数）

对应原 `AutoAnimeMv.py::Sorting_Mv`。
"""

from os import path
from pathlib import Path as PathlibPath

from .. import state
from ..config_loader import Auxiliary_ParseBool
from ..logging_utils import Auxiliary_Log
from ..naming import (
    Auxiliary_ASSFileCA,
    Auxiliary_FormatSEEPToken,
    Auxiliary_SanitizePathComponent,
    Auxiliary_SubtitleLanguageSuffixForEmby,
)
from ..text_utils import Auxiliary_NormalizeChinesePunctuation
from .file_ops import Auxiliary_ExecuteFileOperation


def Sorting_Mv(FileName, RAWName, SE, EP, ASSList, ApiName, SourceFilePath=None):
    '''整理单个文件（含同集字幕）。

    返回主视频（或单字幕）的 `Auxiliary_MakeOperationResult` dict。
    '''
    SourceFilePath = FileName if SourceFilePath in [None, ''] else SourceFilePath
    CategoryName = state.categoryname if state.categoryname not in [None, ''] else ''
    ApiName = ApiName if ApiName else RAWName
    NamingStyle = state.Runtime.config.naming_style if state.Runtime and state.Runtime.config else str(state.NAMING_STYLE).strip().lower()
    NamingStyle = NamingStyle if NamingStyle in ['default', 'emby'] else 'default'
    DryRunMode = state.Runtime.config.dry_run if state.Runtime and state.Runtime.config else Auxiliary_ParseBool(state.DRY_RUN)

    def PcSanitize(Component):
        return Auxiliary_SanitizePathComponent(Auxiliary_NormalizeChinesePunctuation(Component), state.MAX_FILENAME_LENGTH)

    SafeCategory = PcSanitize(CategoryName) if CategoryName != '' else ''
    SafeApiName = PcSanitize(ApiName)
    SEPad = Auxiliary_FormatSEEPToken(SE)
    EPPad = Auxiliary_FormatSEEPToken(EP)

    BaseDir = state.Runtime.output_path if state.Runtime and state.Runtime.output_path else PathlibPath(state.filepath or state.Path or '.')
    if SafeCategory != '':
        BaseDir = BaseDir / SafeCategory

    SeasonDirName = f'Season {SEPad}' if NamingStyle == 'emby' else f'Season{SE}'
    NewDir = BaseDir / SafeApiName / PcSanitize(SeasonDirName)
    if DryRunMode != True:
        NewDir.mkdir(parents=True, exist_ok=True)
    elif NewDir.exists():
        Auxiliary_Log(f'{NewDir}已存在', 'INFO')

    if NamingStyle == 'emby':
        EpisodeBaseName = f'{SafeApiName} - S{SEPad}E{EPPad}'
    else:
        EpisodeBaseName = f'S{SE}E{EP}' if state.USETITLTOEP != True else f'S{SE}E{EP}.{SafeApiName}'
    EpisodeBaseName = PcSanitize(EpisodeBaseName)

    SourceDir = PathlibPath(state.filepath) if state.filepath not in [None, ''] else (
        PathlibPath(state.Path) if state.Path not in [None, ''] else PathlibPath('.')
    )

    if ASSList is not None:
        for ASSFile in ASSList:
            FileType = path.splitext(ASSFile)[1].lower()
            ASSBaseName = path.basename(ASSFile)
            if NamingStyle == 'emby':
                NewASSName = PcSanitize(f'{SafeApiName} - S{SEPad}E{EPPad}{Auxiliary_SubtitleLanguageSuffixForEmby(ASSBaseName)}')
            else:
                NewASSName = PcSanitize(EpisodeBaseName + Auxiliary_ASSFileCA(ASSBaseName))
            DstPath = NewDir / f'{NewASSName}{FileType}'
            SrcPath = SourceDir / ASSFile
            Auxiliary_ExecuteFileOperation(SrcPath, DstPath)

    FileType = path.splitext(FileName)[1].lower()
    if FileType in ['.ass', '.srt']:
        if NamingStyle == 'emby':
            NewName = PcSanitize(f'{SafeApiName} - S{SEPad}E{EPPad}{Auxiliary_SubtitleLanguageSuffixForEmby(FileName)}')
        else:
            NewName = PcSanitize(EpisodeBaseName + Auxiliary_ASSFileCA(FileName))
    else:
        NewName = EpisodeBaseName
    DstPath = NewDir / f'{NewName}{FileType}'
    SrcPath = SourceDir / SourceFilePath
    return Auxiliary_ExecuteFileOperation(SrcPath, DstPath)
