"""
autoanime 字幕相关辅助

对外再导出 `naming.py` 中已落地的：
- `Auxiliary_IDEASS`
- `Auxiliary_ASSFileCA`
- `Auxiliary_SubtitleLanguageSuffixForEmby`

本模块不重复实现，只在 `autoanime.sorting` 子包内提供统一的字幕工具入口。
"""

from ..naming import (
    Auxiliary_ASSFileCA,
    Auxiliary_IDEASS,
    Auxiliary_SubtitleLanguageSuffixForEmby,
)

__all__ = [
    'Auxiliary_IDEASS',
    'Auxiliary_ASSFileCA',
    'Auxiliary_SubtitleLanguageSuffixForEmby',
]
