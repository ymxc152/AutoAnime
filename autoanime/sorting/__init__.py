"""
autoanime 整理链路（sorting）包

| 模块 | 作用 |
| --- | --- |
| `file_ops`  | `Auxiliary_ExecuteFileOperation` / `Auxiliary_MakeOperationResult` / `Auxiliary_IsSamePhysicalFile` |
| `subtitles` | 字幕 IDEASS / ASSFileCA / Emby 语言后缀（再导出 naming.py 中已有实现，保持子包内聚） |
| `pipeline`  | `Sorting_Mv`：主文件 + 字幕一并落盘 |
"""

from .file_ops import (
    Auxiliary_ExecuteFileOperation,
    Auxiliary_IsSamePhysicalFile,
    Auxiliary_MakeOperationResult,
)
from .pipeline import Sorting_Mv
from .subtitles import Auxiliary_IDEASS

__all__ = [
    'Auxiliary_ExecuteFileOperation',
    'Auxiliary_MakeOperationResult',
    'Auxiliary_IsSamePhysicalFile',
    'Auxiliary_IDEASS',
    'Sorting_Mv',
]
