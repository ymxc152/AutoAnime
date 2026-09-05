from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from autoanime.core.enums import Confidence, Segment
from autoanime.core.events import Event

if TYPE_CHECKING:
    from autoanime.pipeline.l3.cache_key import LlmCache
    from autoanime.pipeline.l3.reference import ReferenceFacts

T = TypeVar("T")


@dataclass(frozen=True)
class RawName:
    name: str
    folder: str | None = None
    parent_path: str | None = None


@dataclass(frozen=True)
class ParseContext:
    known_series: int | None = None
    release_progress: int | None = None
    fansub_pref: str | None = None


@dataclass(frozen=True)
class ParseResult:
    title: str
    season: int | None
    episode: int | None
    segment: Segment
    fansub: str | None
    level: Confidence
    confidence: float
    missing_fields: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TorrentSource:
    url: str
    hash: str | None = None
    name: str | None = None


@runtime_checkable
class Recognizer(Protocol):
    async def parse(self, raw: RawName, context: ParseContext | None = None) -> ParseResult | None: ...


@runtime_checkable
class MetadataProvider(Protocol):
    async def search(self, query: str) -> list[dict[str, object]]: ...
    async def get_detail(self, provider_id: int) -> dict[str, object] | None: ...
    async def get_season_episodes(self, provider_id: int, season: int) -> list[dict[str, object]]: ...


@runtime_checkable
class Downloader(Protocol):
    async def add(self, source: TorrentSource) -> str: ...
    async def status(self, torrent_hash: str) -> dict[str, object] | None: ...


@runtime_checkable
class Notifier(Protocol):
    async def send(self, event: Event) -> None: ...


@runtime_checkable
class Storage(Protocol):
    async def create_all(self) -> None: ...
    async def add(self, obj: Any) -> None: ...
    async def get(self, model: type[Any], id_: int) -> Any | None: ...
    async def list(self, model: type[Any]) -> list[Any]: ...
    async def delete(self, obj: Any) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class MemoryStore(Protocol):
    """L2 memory persistence contract (PR4).

    Implementations keep every DB session internally; callers only pass the
    store object itself. The ORM row is ``autoanime.core.models.ParseMemory``;
    it is typed as ``Any`` here so this module stays free of SQLAlchemy
    imports, mirroring the ``Storage`` protocol style.
    """

    async def find_parse_memory(self, key_level: int, key_hash: str) -> Any | None: ...
    async def record_hit(
        self, parse_memory: Any, *, operation_id: str | None = None
    ) -> None: ...
    async def record_correction(self, parse_memory: Any) -> None: ...
    async def has_bypass(self, pattern_hash: str) -> bool: ...


@runtime_checkable
class MemoryRecognizer(Protocol):
    """L2 memory enhancement contract (PR4).

    Input: the L1 ParseResult, the optional parse context, and the injected
    memory store. Output: the enhanced ParseResult on a hit; ``None`` when
    the memory has nothing to add, in which case the orchestrator routes by
    the L1 result alone.
    """

    async def enhance(
        self,
        result: ParseResult,
        context: ParseContext | None,
        store: MemoryStore,
        *,
        operation_id: str | None = None,
    ) -> ParseResult | None: ...


@runtime_checkable
class LlmTransport(Protocol):
    """L3 的唯一网络出口（PR5 T3 实现，测试用 fake）。

    一次调用即一次 LLM 补全。客户端、endpoint、密钥全部由实现持有；
    重试与预算由 L3 纯函数层（pipeline.l3.budget）判定，不进 transport。
    """

    async def complete(self, prompt: str, *, model: str, timeout_s: float) -> str: ...


@runtime_checkable
class LlmCacheStore(Protocol):
    """llm_cache 持久化契约（PR5 T2 实现，测试用 fake）。

    以 L3 pattern hash（与 bypass 兼容的规范化摘要）为键。``get`` 返回
    该 pattern 录制的缓存响应；``put`` 只存真实调用成功（schema 合法）的
    响应，失败与非法响应一律不落缓存。DB 会话只存在于实现内部。
    """

    async def get(self, pattern_hash: str) -> LlmCache | None: ...
    async def put(self, cache: LlmCache) -> None: ...


@runtime_checkable
class MetadataReference(Protocol):
    """参考源查询契约（PR6 实现真实 Bangumi/TMDB provider）。

    入参 ``title_shape`` 是 L2 title shape 形式（casefold、占位符化）；
    命中返回参考事实，未命中返回 ``None``。各 provider 通过 Registry 注册
    （``@registry.register(MetadataReference, "bangumi")``），由
    ReferenceChain 按 ``reference_order`` 组合成链。
    """

    async def lookup(self, title_shape: str) -> ReferenceFacts | None: ...


@runtime_checkable
class L3Recognizer(Protocol):
    """L3 LLM fallback 契约（PR5 T2/T3 实现）。

    输入：raw name、L1 结果（可为 ``None``）、解析上下文、注入的
    transport 与 cache store。输出：独立的 L3 层 ParseResult（出现的
    字段一律 evidence=``llm``、level MEDIUM，交 arbiter 合并），或
    ``None``——L3 未启用/不可用/解析失败时交回 orchestrator 按 L1/L2
    原结果路由（R7）。

    调用边界：每个 raw_name 最多一次真实 LLM 调用（重试除外），先查
    llm_cache（pattern_hash），未命中才真实调用。
    """

    async def enhance(
        self,
        raw: RawName,
        result: ParseResult | None,
        context: ParseContext | None,
        transport: LlmTransport,
        cache_store: LlmCacheStore,
        *,
        operation_id: str | None = None,
    ) -> ParseResult | None: ...


class Registry:
    """Small explicit registry used only for external providers and gateways."""

    def __init__(self) -> None:
        self._services: dict[type[object], dict[str, object]] = {}

    def register(self, protocol: type[object], name: str) -> Callable[[T], T]:
        def decorator(value: T) -> T:
            bucket = self._services.setdefault(protocol, {})
            bucket[name] = value
            return value

        return decorator

    def get(self, protocol: type[object], name: str | None = None) -> object:
        bucket = self._services.get(protocol)
        if bucket is None:
            raise KeyError(protocol)
        if name is None:
            if len(bucket) != 1:
                raise KeyError(f"protocol has multiple implementations: {protocol}")
            return next(iter(bucket.values()))
        if name not in bucket:
            raise KeyError(name)
        return bucket[name]

    def optional(self, protocol: type[object], name: str | None = None) -> object | None:
        try:
            return self.get(protocol, name)
        except KeyError:
            return None
