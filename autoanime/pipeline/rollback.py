"""
autoanime 回滚

对应原 `AutoAnimeMv.py::Auxiliary_RollbackFromLog`。
"""

import json

from os import remove
from pathlib import Path as PathlibPath
from shutil import move

from ..logging_utils import Auxiliary_Exit, Auxiliary_Log


def Auxiliary_RollbackFromLog(LogPath):
    '''根据操作日志回滚文件。'''
    RollbackFile = PathlibPath(LogPath)
    if RollbackFile.is_file() == False:
        Auxiliary_Exit(f'回滚日志不存在: {RollbackFile}')
    try:
        with open(RollbackFile, 'r', encoding='UTF-8') as ff:
            Data = json.load(ff)
    except json.JSONDecodeError:
        with open(RollbackFile, 'r', encoding='UTF-8-sig') as ff:
            Data = json.load(ff)
    Records = Data.get('records', [])
    if type(Records) != list or Records == []:
        Auxiliary_Exit(f'回滚日志内无可用记录: {RollbackFile}')
    for Record in Records[::-1]:
        if type(Record) != dict:
            continue
        if Record.get('status') not in ['success']:
            continue
        Action = Record.get('action')
        SrcPath = PathlibPath(Record.get('src', ''))
        DstPath = PathlibPath(Record.get('dst', ''))
        BackupPath = PathlibPath(Record.get('backup')) if Record.get('backup') not in [None, ''] else None
        try:
            if Action == 'move':
                if DstPath.exists():
                    DstPath.parent.mkdir(parents=True, exist_ok=True)
                    move(str(DstPath), str(SrcPath))
                if BackupPath and BackupPath.exists():
                    move(str(BackupPath), str(DstPath))
            elif Action == 'link':
                if DstPath.exists():
                    remove(str(DstPath))
                if BackupPath and BackupPath.exists():
                    move(str(BackupPath), str(DstPath))
            elif Action == 'remove':
                if BackupPath and BackupPath.exists():
                    move(str(BackupPath), str(DstPath))
            Auxiliary_Log(f'回滚成功: {Action} {DstPath} -> {SrcPath}', 'INFO')
        except Exception as err:
            Auxiliary_Log(f'回滚失败: {Action} {DstPath} -> {SrcPath}, {err}', 'WARNING')
