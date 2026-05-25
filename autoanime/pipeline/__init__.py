"""
autoanime 主流水线包

| 模块 | 作用 |
| --- | --- |
| `mode`          | `Processing_Mode`：目录扫描/单文件短路 |
| `main`          | `Processing_Main`：遍历识别 + 调度整理 |
| `operation_log` | `Auxiliary_RecordOperation` / `Auxiliary_WriteOperationLog` |
| `rollback`      | `Auxiliary_RollbackFromLog` |
"""

from .main import Processing_Main
from .mode import Processing_Mode
from .operation_log import Auxiliary_RecordOperation, Auxiliary_WriteOperationLog
from .rollback import Auxiliary_RollbackFromLog

__all__ = [
    'Processing_Mode',
    'Processing_Main',
    'Auxiliary_RecordOperation',
    'Auxiliary_WriteOperationLog',
    'Auxiliary_RollbackFromLog',
]
