"""MetadataReference 契约对象与 ReferenceChain 组合器。

``ReferenceFacts`` 是参考源的中立事实包（canonical_title / seasons /
episode_count / aliases / source）。各 provider 通过 Registry 注册：

    @registry.register(MetadataReference, "bangumi")

``ReferenceChain`` 在构造时按 ``reference_order`` 从 Registry 解析一次
（未注册的名字跳过，容错部分注册），调用时按链序逐个 lookup，第一个
命中即用；``enabled=False``（reference_enabled）整体关闭，直接返回
``None``。链序即优先级。

本模块只做组合编排：无 DB 会话、无网络——真实 provider（PR6）自带
transport 并自行注册。真实 provider 的异常由上层编排（T5）按优雅降级
处理，链本身如实传播。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from autoanime.core.interfaces import MetadataReference, Registry


@dataclass(frozen=True)
class ReferenceFacts:
    """一次参考源命中的中立事实包。

    ``seasons`` 是该作品已知的季列表（多值时供 R6 消歧）；``source``
    是产出事实的 provider 注册名，供 arbiter 审计。
    """

    canonical_title: str | None = None
    seasons: tuple[int, ...] = ()
    episode_count: int | None = None
    aliases: tuple[str, ...] = ()
    source: str | None = None
    poster_url: str | None = None
    """参考源侧海报图直链（无则 None）；仅来自 adapter 响应，非用户输入。"""


class ReferenceChain:
    """按 reference_order 组合的参考源链：链序即优先级，第一个命中即用。"""

    def __init__(
        self,
        registry: Registry,
        *,
        order: Sequence[str] = ("bangumi", "tmdb"),
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        resolved: list[tuple[str, MetadataReference]] = []
        for name in order:
            provider = registry.optional(MetadataReference, name)
            if isinstance(provider, MetadataReference):
                resolved.append((name, provider))
        self._providers: tuple[tuple[str, MetadataReference], ...] = tuple(resolved)

    @property
    def names(self) -> tuple[str, ...]:
        """实际解析进链的 provider 名（按优先级序），供审计。"""
        return tuple(name for name, _provider in self._providers)

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        """按链序查询；整体关闭或全部未命中返回 ``None``。"""
        if not self._enabled:
            return None
        for _name, provider in self._providers:
            facts = await provider.lookup(title_shape)
            if facts is not None:
                return facts
        return None
