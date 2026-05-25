"""
autoanime 操作日志

对应原 `AutoAnimeMv.py`:
- `Auxiliary_RecordOperation`
- `Auxiliary_WriteOperationLog`
"""

import json

from time import localtime, strftime, time

from .. import state
from ..logging_utils import Auxiliary_Log


def Auxiliary_RecordOperation(Action, SrcPath, DstPath, Status, Message='', BackupPath=''):
    if state.Runtime is None:
        return
    state.Runtime.operation_records.append({
        'timestamp': strftime('%Y-%m-%d %H:%M:%S', localtime(time())),
        'action': Action,
        'src': str(SrcPath),
        'dst': str(DstPath),
        'status': Status,
        'message': Message,
        'backup': str(BackupPath) if BackupPath not in [None, ''] else '',
    })


def Auxiliary_WriteOperationLog():
    if state.OPERATION_LOG_ENABLE != True or state.Runtime is None:
        return
    if state.RUN_COMMAND == 'rollback':
        return
    if state.Runtime.operation_log_path in [None, '']:
        return
    try:
        state.Runtime.operation_log_path.parent.mkdir(parents=True, exist_ok=True)
        Payload = {
            'run_id': state.CurrentRunID,
            'dry_run': state.Runtime.config.dry_run,
            'naming_style': state.Runtime.config.naming_style,
            'records': state.Runtime.operation_records,
        }
        with open(state.Runtime.operation_log_path, 'w', encoding='UTF-8') as LogFile:
            json.dump(Payload, LogFile, ensure_ascii=False, indent=2)
        Auxiliary_Log(f'操作日志已写入 {state.Runtime.operation_log_path}', 'INFO')
    except Exception as err:
        Auxiliary_Log(f'操作日志写入失败: {err}', 'WARNING')
