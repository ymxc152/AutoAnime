"""
autoanime 扫描工具

对应原 `AutoAnimeMv.py`:
- `Auxiliary_ScanDIR`
- `Auxiliary_IsIncompleteDownloadFile`
- `Auxiliary_ScanEntryShouldSkip`
- `Auxiliary_DefaultScanSkipPathMarkers`
- `Auxiliary_DefaultScanSkipNameRegexStrings`
- `Auxiliary_GetScanSkipPathMarkers`
- `Auxiliary_GetScanSkipNameRegexList`

额外提供：
- `NormalizeSingleFileInput`: 配合 CLI 的单文件输入（目录/完整文件路径/目录+文件名）归一化。
"""

from os import path
from pathlib import Path as PathlibPath
from re import I, compile as re_compile, search

from . import state
from .logging_utils import Auxiliary_Exit, Auxiliary_Log, Auxiliary_FormatListPreview


def Auxiliary_DefaultScanSkipPathMarkers():
    return [
        'SP', 'SPs', 'OP', 'ED', 'PV', 'PVs', 'NCOP', 'NCED', 'NCOPs', 'NCEDs',
        'Special', 'Specials', 'Extra', 'Extras', 'Bonus', 'Menus', 'Menu',
        'Creditless', 'Clean', 'CM', 'Preview', 'Previews', 'Trailer', 'Teasers',
        'Scans', 'Scan', 'Making', 'Interview', 'Tokuten', 'Drama',
    ]


def Auxiliary_DefaultScanSkipNameRegexStrings():
    return [
        r'(?i)\bNCOP\d*\b',
        r'(?i)\bNCED\d*\b',
        r'(?i)Non-?Credit',
        r'(?i)\bMenu\d+\b',
        r'(?i)\b(PV|Preview|CM)\d*\b',
    ]


def Auxiliary_GetScanSkipPathMarkers():
    Markers = state.SCAN_SKIP_PATH_MARKERS
    if type(Markers) != list or Markers == []:
        return Auxiliary_DefaultScanSkipPathMarkers()
    return [str(M).strip() for M in Markers if str(M).strip() not in [None, '']]


def Auxiliary_GetScanSkipNameRegexList():
    Patterns = state.SCAN_SKIP_NAME_REGEX
    if type(Patterns) != list or Patterns == []:
        PatternStrings = Auxiliary_DefaultScanSkipNameRegexStrings()
    else:
        PatternStrings = [str(P).strip() for P in Patterns if str(P).strip() not in [None, '']]
    CompiledList = []
    for PatternStr in PatternStrings:
        try:
            CompiledList.append(re_compile(PatternStr))
        except Exception:
            continue
    return CompiledList


def Auxiliary_ScanEntryShouldSkip(RelativeFileNormalized, BaseName):
    PathLower = RelativeFileNormalized.lower()
    Segments = [Seg for Seg in PathLower.split('/') if Seg not in [None, '']]
    MarkerList = [M.lower() for M in Auxiliary_GetScanSkipPathMarkers()]
    for Segment in Segments[:-1]:
        for Marker in MarkerList:
            if Marker in [None, '']:
                continue
            if Segment == Marker or Segment.startswith(Marker + '.') or Segment.startswith(Marker + '_'):
                return True
    for RegexObj in Auxiliary_GetScanSkipNameRegexList():
        try:
            if RegexObj.search(BaseName) != None:
                return True
        except Exception:
            continue
    return False


def Auxiliary_IsIncompleteDownloadFile(FileName) -> bool:
    '''判断是否为未完成下载文件'''
    BaseName = path.basename(str(FileName)).lower()
    IncompleteSuffixes = ('.!qb', '.part', '.partial', '.aria2', '.crdownload')
    return BaseName.endswith(IncompleteSuffixes)


def Auxiliary_ScanDIR(Dir, Flag=0) -> list:
    '''扫描文件目录,返回文件列表'''

    def Scan(RelativeFile):
        FileSuffix = path.splitext(RelativeFile)[1].lower()
        if FileSuffix == '.ass' or FileSuffix == '.srt':
            AssFileList.append(RelativeFile)
        elif FileSuffix == '.log':
            LogsFileList.append(RelativeFile)
        elif FileSuffix == '.mp4' or FileSuffix == '.mkv':
            VDFileList.append(RelativeFile)

    SuffixList = ['.ass', '.srt', '.mp4', '.mkv', '.log']
    AssFileList = []
    VDFileList = []
    LogsFileList = []
    RootPath = PathlibPath(Dir)
    OutputRelativePrefix = None
    if state.Runtime and getattr(state.Runtime, 'output_path', None):
        try:
            OutputRelativePrefix = str(PathlibPath(state.Runtime.output_path).resolve().relative_to(RootPath.resolve())).replace('\\', '/')
            if OutputRelativePrefix in ['', '.']:
                OutputRelativePrefix = None
        except Exception:
            OutputRelativePrefix = None
    for Entry in RootPath.rglob('*'):
        if Entry.is_file() == False:
            continue
        RelativeFile = str(Entry.relative_to(RootPath))
        RelativeFileNormalized = RelativeFile.replace('\\', '/')
        if OutputRelativePrefix not in [None, ''] and (
            RelativeFileNormalized == OutputRelativePrefix or RelativeFileNormalized.startswith(f'{OutputRelativePrefix}/')
        ):
            continue
        BaseName = path.basename(RelativeFile)
        if path.splitext(BaseName)[1].lower() not in SuffixList:
            continue
        if Auxiliary_ScanEntryShouldSkip(RelativeFileNormalized, BaseName):
            continue
        if Flag == 0 and search(r'S\d{1,2}E\d{1,4}', BaseName, flags=I) == None:
            Scan(RelativeFile)
        elif Flag == 1 and search(r'S\d{1,2}E\d{1,4}', BaseName, flags=I) != None:
            Scan(RelativeFile)

    # 同步日志清理所需的文件列表
    state.LogsFileList = LogsFileList

    if VDFileList != []:
        if AssFileList != []:
            Auxiliary_Log(
                (
                    f'发现{len(AssFileList)}个字幕文件 ==> {Auxiliary_FormatListPreview(AssFileList)}',
                    f'发现{len(VDFileList)}个视频文件 ==> {Auxiliary_FormatListPreview(VDFileList)}',
                ),
                'INFO',
            )
            return VDFileList, AssFileList
        Auxiliary_Log(
            f'发现{len(VDFileList)}个视频文件,没有发现字幕文件 ==> {Auxiliary_FormatListPreview(VDFileList)}',
            'INFO',
        )
        return VDFileList
    elif AssFileList != []:
        Auxiliary_Log(
            (
                f'没有发现任何番剧视频文件,但发现{len(AssFileList)}个字幕文件 ==> {Auxiliary_FormatListPreview(AssFileList)}',
                '只有字幕文件需要处理',
            ),
            'INFO',
        )
        return AssFileList
    else:
        Auxiliary_Exit('没有任何番剧文件')


def NormalizeSingleFileInput(InputPath: str):
    '''
    配合 CLI 的新单文件输入做归一化。

    返回：`(effective_dir, filenames_or_None, single_file_mode)`；
    - 若 `InputPath` 是目录：`(dir, None, False)`；
    - 若 `InputPath` 是文件：`(dir=parent, [basename], True)`；该文件若为视频，同目录下同基名开头的 `.ass/.srt` 一并收录。
    - 若都不是：`(InputPath, None, False)`（留给后续逻辑/Exit）。
    '''
    if InputPath in [None, '']:
        return InputPath, None, False
    P = PathlibPath(InputPath)
    if P.is_file():
        Parent = str(P.parent)
        SubtitleList = []
        Base = P.stem
        Video = P.suffix.lower() in ('.mp4', '.mkv')
        if Video:
            for Sib in P.parent.iterdir():
                if Sib.is_file() == False:
                    continue
                if Sib.suffix.lower() in ('.ass', '.srt') and Sib.stem.startswith(Base):
                    SubtitleList.append(Sib.name)
        FileNames = [P.name] + SubtitleList
        return Parent, FileNames, True
    if P.is_dir():
        return InputPath, None, False
    return InputPath, None, False
