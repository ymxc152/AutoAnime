"""外部 provider 适配器（参考源、gateway）。

PR6：Bangumi / TMDB 两个 ``MetadataReference`` 参考源插件的注册入口。
Registry 是显式传入的（``Registry`` 实例由装配方持有），本包不设模块级
全局 registry（PR6 规则 5：不引入模块级可变全局状态）。
"""

from __future__ import annotations

from autoanime.core.interfaces import MetadataReference, Registry
from autoanime.providers.bangumi import (
    ANIME_SUBJECT_TYPE,
    BANGUMI_BASE_URL,
    USER_AGENT,
    BangumiReference,
)
from autoanime.providers.tmdb import (
    DEFAULT_LANGUAGE,
    TMDB_API_KEY_ENV,
    TMDB_BASE_URL,
    TmdbReference,
)

__all__ = [
    "ANIME_SUBJECT_TYPE",
    "BANGUMI_BASE_URL",
    "BangumiReference",
    "DEFAULT_LANGUAGE",
    "TMDB_API_KEY_ENV",
    "TMDB_BASE_URL",
    "TmdbReference",
    "USER_AGENT",
    "register_reference_providers",
]


def register_reference_providers(registry: Registry) -> None:
    """把 Bangumi/TMDB 参考源实例注册进显式 Registry。

    注册名与 ``reference_order`` 默认链序（``["bangumi", "tmdb"]``）一致。
    每次调用创建新实例（频控状态等实例状态随装配边界重置）；重复注册
    同名插件按 Registry 语义覆盖。TMDB 未配置 ``AUTOANIME_TMDB_API_KEY``
    时仍注册其实例（``lookup`` 直接 miss，链继续问下一个 provider）。
    """
    registry.register(MetadataReference, "bangumi")(BangumiReference())
    registry.register(MetadataReference, "tmdb")(TmdbReference())
