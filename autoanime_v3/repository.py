"""资料库公共入口。

底层当前使用 SQLite；CLI、未来 WebUI 和应用服务只依赖 ``LibraryRepository``，
不依赖具体表或文件格式。
"""

from .cache import ResolutionCache as LibraryRepository, fingerprint

__all__ = ["LibraryRepository", "fingerprint"]
