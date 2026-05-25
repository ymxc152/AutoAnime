"""
autoanime 文件操作执行器（move/link/dry-run + 覆盖备份）

对应原 `AutoAnimeMv.py`:
- `Auxiliary_IsSamePhysicalFile`
- `Auxiliary_MakeOperationResult`
- `Auxiliary_ExecuteFileOperation`

`Auxiliary_RecordOperation` 迁至 `autoanime.pipeline.operation_log`。
"""

from os import link
from pathlib import Path as PathlibPath
from shutil import move

from .. import state
from ..config_loader import Auxiliary_ParseBool
from ..logging_utils import Auxiliary_Log
from ..pipeline.operation_log import Auxiliary_RecordOperation


def Auxiliary_IsSamePhysicalFile(LeftPath, RightPath) -> bool:
    '''判断两个路径是否指向同一个物理文件（含硬链接）'''
    LeftPath = PathlibPath(LeftPath)
    RightPath = PathlibPath(RightPath)
    try:
        if LeftPath.exists() and RightPath.exists():
            return LeftPath.samefile(RightPath)
    except Exception:
        return False
    return False


def Auxiliary_MakeOperationResult(Action, SrcPath, DstPath, Status, Message='', BackupPath=''):
    return {
        'action': Action,
        'src': str(SrcPath),
        'dst': str(DstPath),
        'status': Status,
        'message': Message,
        'backup': str(BackupPath) if BackupPath not in [None, ''] else '',
    }


def Auxiliary_ExecuteFileOperation(SrcPath, DstPath):
    '''执行 move/link，支持 dry-run 与覆盖备份。'''
    SrcPath = PathlibPath(SrcPath)
    DstPath = PathlibPath(DstPath)
    BackupPath = ''
    ActionName = 'link' if state.USELINK == True else 'move'
    DryRunMode = state.Runtime.config.dry_run if state.Runtime and state.Runtime.config else Auxiliary_ParseBool(state.DRY_RUN)
    StrictMode = state.Runtime.config.strict_mode if state.Runtime and state.Runtime.config else Auxiliary_ParseBool(state.STRICT_MODE)

    if SrcPath.is_file() == False:
        Auxiliary_Log(f'源文件不存在，跳过: {SrcPath}', 'WARNING')
        Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'skipped', 'src_not_found')
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'src_not_found')

    if DstPath.exists():
        if Auxiliary_IsSamePhysicalFile(SrcPath, DstPath):
            Auxiliary_Log(f'目标文件已与源文件一致，跳过重复整理: {DstPath}', 'INFO')
            Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'skipped', 'same_file')
            return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'same_file')
        if state.USELINK == True:
            Auxiliary_Log(f'目标文件已存在，保留原有硬链接，跳过替换: {DstPath}', 'INFO')
            Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'skipped', 'existing_link_kept')
            return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'existing_link_kept')
        if state.MANDATORYCOVER != True:
            Auxiliary_Log(f'{DstPath}已存在,故跳过', 'WARNING')
            Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'skipped', 'target_exists')
            return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'target_exists')
        BackupPath = DstPath.with_name(f'{DstPath.name}.aam.bak.{state.CurrentRunID}')
        if DryRunMode == True:
            Auxiliary_Log(f'DRY_RUN: 预览覆盖备份 {DstPath} -> {BackupPath}', 'INFO')
        else:
            DstPath.parent.mkdir(parents=True, exist_ok=True)
            move(str(DstPath), str(BackupPath))
            Auxiliary_Log(f'覆盖前备份: {DstPath} -> {BackupPath}', 'INFO')

    if DryRunMode == True:
        Auxiliary_Log(f'DRY_RUN: 预览{ActionName.upper()} {SrcPath} -> {DstPath}', 'INFO')
        Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'dry-run', 'preview', BackupPath)
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'dry-run', 'preview', BackupPath)

    try:
        DstPath.parent.mkdir(parents=True, exist_ok=True)
        if state.USELINK == True:
            try:
                link(str(SrcPath), str(DstPath))
            except OSError as err:
                if '[WinError 1]' in str(err):
                    if StrictMode == True:
                        Auxiliary_Log('严格模式开启：硬链接失败后不会降级移动，已跳过当前文件', 'ERROR')
                        Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'failed', 'strict_mode_link_failed', BackupPath)
                        if BackupPath not in ['', None] and PathlibPath(BackupPath).exists():
                            try:
                                move(str(BackupPath), str(DstPath))
                            except Exception:
                                pass
                        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'failed', 'strict_mode_link_failed', BackupPath)
                    if state.LINKFAILSUSEMOVEFLAGS == True:
                        Auxiliary_Log('当前文件系统不支持硬链接，自动回退到 move', 'WARNING')
                        move(str(SrcPath), str(DstPath))
                        ActionName = 'move'
                    else:
                        raise err
                else:
                    raise err
        else:
            move(str(SrcPath), str(DstPath))
        Auxiliary_Log(f'{ActionName.upper()}-{DstPath} << {SrcPath}', 'INFO')
        Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'success', '', BackupPath)
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'success', '', BackupPath)
    except Exception as err:
        if BackupPath not in ['', None] and PathlibPath(BackupPath).exists() and DstPath.exists() == False:
            try:
                move(str(BackupPath), str(DstPath))
            except Exception:
                pass
        Auxiliary_Log(f'文件操作失败 {SrcPath} -> {DstPath}: {err}', 'ERROR')
        Auxiliary_RecordOperation(ActionName, SrcPath, DstPath, 'failed', str(err), BackupPath)
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'failed', str(err), BackupPath)
