"""
autoanime Processing_Mode

对应原 `AutoAnimeMv.py::Processing_Mode`，并新增：
- 单文件模式短路（state.SingleFileMode == True）：
    直接返回 `[video_filename]` 或 `(视频列表, 字幕列表)`，避免 `rglob` 扫描误伤同目录其它剧集。
- 字幕附属匹配：同目录下以视频基名开头的 `.ass/.srt` 被视为附属字幕（由 CLI 的
  `NormalizeSingleFileInput` 预先收入 `state.SingleFileSubtitles`）。
"""

from os import path

from .. import state
from ..config_loader import Auxiliary_InitRuntimeContext
from ..logging_utils import Auxiliary_Exit, Auxiliary_Log
from ..scanning import Auxiliary_IsIncompleteDownloadFile, Auxiliary_ScanDIR


def _LogDeleteLogsIfAvailable():
    '''调用旧的日志清理函数（若存在）。当前模块化下本函数保留占位。'''
    # 旧 Auxiliary_DeleteLogs 依赖旧模块 globals；本包暂未迁移清理逻辑，
    # 兼容期内由上层旧入口负责；此处留空即可。
    return


def Processing_Mode(ArgvData):
    '''模式选择：返回需要处理的文件列表（或 (videos, subtitles) 二元组）。

    - 单文件模式（`state.SingleFileMode == True`）：短路返回，仅此一文件 + 同目录字幕附属；
    - qB 回调模式（ArgvData 为 `(dir, file, "1", ...)`）：仅整理该文件；
    - 其它情形：走 `Auxiliary_ScanDIR` 递归扫描。
    '''
    ArgvNumber = len(ArgvData) if type(ArgvData) in [list, tuple] else 1
    state.Path = state.filepath
    state.CategoryName = state.categoryname
    Auxiliary_InitRuntimeContext()

    if path.exists(state.Path) != True:
        Auxiliary_Exit(f'不存在 {state.Path} 目录')

    if state.CategoryName:
        Auxiliary_Log(f'当前分类 >> {state.CategoryName}')

    # 单文件模式优先级最高
    if state.SingleFileMode == True and state.SingleFileVideoName not in [None, '']:
        VideoName = state.SingleFileVideoName
        VideoAbs = path.join(state.Path, VideoName)
        if path.isfile(VideoAbs) == False:
            Auxiliary_Exit(f'单文件模式下指定的文件不存在: {VideoAbs}')
        if Auxiliary_IsIncompleteDownloadFile(VideoName):
            Auxiliary_Log(f'单文件输入为未完成下载文件，跳过: {VideoName}', 'WARNING')
            return []
        SubtitleNames = [S for S in (state.SingleFileSubtitles or []) if path.isfile(path.join(state.Path, S))]
        Auxiliary_Log(f'单文件模式：视频={VideoName} 字幕={SubtitleNames}', 'INFO')
        VideoExt = path.splitext(VideoName)[1].lower()
        if VideoExt in ('.ass', '.srt'):
            # 单个字幕文件独立整理
            return [VideoName]
        if SubtitleNames:
            return [VideoName], SubtitleNames
        return [VideoName]

    # qB 回调模式：位置参数 2 == '1' 时单文件整理
    if type(ArgvData) in [list, tuple] and ArgvNumber >= 3 and str(ArgvData[2]) == '1' and ArgvData[1] not in [None, '']:
        FileListTuporList = [ArgvData[1]]
    else:
        FileListTuporList = Auxiliary_ScanDIR(state.Path)

    _LogDeleteLogsIfAvailable()

    if type(FileListTuporList) == tuple:
        return FileListTuporList

    valid_files = []
    skipped_incomplete_files = []
    for i in FileListTuporList:
        AbsPath = path.join(state.Path, i)
        if path.isfile(AbsPath):
            if Auxiliary_IsIncompleteDownloadFile(i):
                skipped_incomplete_files.append(i)
                Auxiliary_Log(f'跳过未完成下载文件: {i}', 'INFO')
                continue
            valid_files.append(i)
        else:
            Auxiliary_Log(f'{AbsPath} 不存在的文件', 'WARNING')
    if valid_files:
        return valid_files
    if skipped_incomplete_files:
        Auxiliary_Log('本次仅检测到未完成下载文件，已全部跳过', 'INFO')
        return []
    Auxiliary_Exit('没有有效的番剧文件')
