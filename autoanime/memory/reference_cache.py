"""参考源剧目级缓存与频控（PR6 P2）。

三个职责：

1. ``ReferenceCacheStore``：reference_cache 持久化的窄 Protocol——T1 定稿的
   ``MetadataReference``/``ReferenceChain`` 契约不含缓存语义，故在本模块
   内定稿；``SqliteStorage`` 的同名方法按结构化子类型满足该协议；
2. ``CachedReference``：``MetadataReference`` 装饰器——先查缓存（命中且
   未过期直接返回，含负缓存），未命中才调被包装的真实 adapter，成功
   （含「查无结果」）后写缓存。缓存命中路径不经过频控；
3. token bucket 频控：``refill_tokens``/``compute_bucket_wait`` 纯函数 +
   ``TokenBucketLimiter`` 实例状态——挂在装配出的 provider 实例上，不进
   模块级全局（PR6 规则 5）。

缓存语义（默认值，可按实例覆盖）：
- 正缓存（查到 facts）TTL 30 天：权威参考数据近乎静态，30 天足以覆盖
  一个播出季，同时限制连载中剧集数漂移的陈旧窗口；
- 负缓存（adapter 返回 ``None``）TTL 24 小时：防批量扫描时对同一查无
  结果的 title shape 反复打 API；TTL 取短，让新登记的条目次日即可被
  发现。

时间基准：TTL 判定用注入的 ``now_fn``（默认 ``datetime.now``，与模型层
``fetched_at``/``expires_at`` 一致的 naive 本地时间）；频控用单调 clock。
所有缓存读写失败按 miss 处理并记日志，绝不阻断参考链。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from autoanime.core.interfaces import MetadataReference
from autoanime.core.models import ReferenceCache
from autoanime.pipeline.l3.reference import ReferenceFacts

logger = logging.getLogger(__name__)

DEFAULT_POSITIVE_TTL_S = 30 * 24 * 3600.0
"""正缓存默认 TTL：30 天（理由见模块 docstring）。"""

DEFAULT_NEGATIVE_TTL_S = 24 * 3600.0
"""负缓存默认 TTL：24 小时（理由见模块 docstring）。"""

_NEGATIVE_KEY = "negative"
"""负缓存在 ``facts`` JSON 中的标记键。"""


@runtime_checkable
class ReferenceCacheStore(Protocol):
    """reference_cache 持久化契约（PR6 P2，本模块内定稿的窄 Protocol）。

    T1 的参考源契约未定义缓存 Protocol，故在此补窄协议：实现方（
    ``SqliteStorage``）内部持 DB 会话，调用方只传对象；是否过期与
    ``facts`` JSON 解释由 ``CachedReference`` 负责，store 层只存取。
    """

    async def find_reference_cache(
        self, title_shape: str, provider: str
    ) -> ReferenceCache | None: ...

    async def add_reference_cache(self, row: ReferenceCache) -> None: ...


# ---------------------------------------------------------------------------
# facts JSON 序列化（负缓存用标记字典区分）
# ---------------------------------------------------------------------------


def facts_to_json(facts: ReferenceFacts) -> dict[str, object]:
    """``ReferenceFacts`` → 可存 ``facts`` 列的 JSON 字典。"""
    return {
        "canonical_title": facts.canonical_title,
        "seasons": list(facts.seasons),
        "episode_count": facts.episode_count,
        "aliases": list(facts.aliases),
        "source": facts.source,
        "poster_url": facts.poster_url,
    }


def is_negative_json(data: object) -> bool:
    """``facts`` JSON 是否为负缓存标记（``{"negative": true}``）。"""
    return isinstance(data, dict) and data.get(_NEGATIVE_KEY) is True


def _int_tuple(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, int) and not isinstance(item, bool))


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def facts_from_json(data: object) -> ReferenceFacts | None:
    """``facts`` JSON → ``ReferenceFacts``；负缓存标记或形状不符返回 ``None``。

    字段逐一防御式解析：类型不符的字段落回默认值（而不是整条作废），
    交付数据库里的历史/手写数据。
    """
    if not isinstance(data, dict) or is_negative_json(data):
        return None
    canonical = data.get("canonical_title")
    episode_count = data.get("episode_count")
    source = data.get("source")
    return ReferenceFacts(
        canonical_title=canonical if isinstance(canonical, str) else None,
        seasons=_int_tuple(data.get("seasons")),
        episode_count=(
            episode_count
            if isinstance(episode_count, int) and not isinstance(episode_count, bool)
            else None
        ),
        aliases=_str_tuple(data.get("aliases")),
        source=source if isinstance(source, str) else None,
        poster_url=(data["poster_url"] if isinstance(data.get("poster_url"), str) else None),
    )


# ---------------------------------------------------------------------------
# token bucket 频控（纯函数 + 实例状态）
# ---------------------------------------------------------------------------


def refill_tokens(
    tokens: float,
    last_refill_at: float | None,
    now: float,
    *,
    rate: float,
    capacity: float,
) -> float:
    """按流逝时间补充令牌并封顶 ``capacity``（纯函数）。"""
    if last_refill_at is None:
        return capacity
    elapsed = max(0.0, now - last_refill_at)
    return min(capacity, tokens + elapsed * rate)


def compute_bucket_wait(tokens: float, *, rate: float) -> float:
    """取 1 个令牌还需等待的秒数（非负）；``rate <= 0`` 视为不限流。"""
    if rate <= 0.0:
        return 0.0
    return max(0.0, (1.0 - tokens) / rate)


class TokenBucketLimiter:
    """每 provider 的 token bucket 频控。

    实例状态（``_tokens``/``_last_refill_at``）挂在装配出的 provider 实例
    上，随装配边界重置，不进模块级全局（PR6 规则 5）。``qps`` 是令牌补充
    速率；容量默认 ``max(1.0, qps)``（允许一次突发）。``qps <= 0`` 时
    ``acquire`` 直通（不限流）——此时由 P1 adapter 内部的 HTTP 层节流兜底。
    """

    def __init__(
        self,
        *,
        qps: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._rate = qps
        self._capacity = capacity if capacity is not None else max(1.0, qps)
        self._clock = clock
        self._sleeper = sleeper
        self._tokens = self._capacity
        self._last_refill_at: float | None = None

    @property
    def limited(self) -> bool:
        """是否处于限流状态（``qps``/``capacity`` 均为正）。"""
        return self._rate > 0.0 and self._capacity > 0.0

    async def acquire(self) -> None:
        """取 1 个令牌；不足时按等待时间睡眠后再取。"""
        if not self.limited:
            return
        now = self._clock()
        tokens = refill_tokens(
            self._tokens, self._last_refill_at, now, rate=self._rate, capacity=self._capacity
        )
        wait = compute_bucket_wait(tokens, rate=self._rate)
        if wait > 0.0:
            await self._sleeper(wait)
            tokens = min(self._capacity, tokens + wait * self._rate)
            now = self._clock()
        self._tokens = max(0.0, tokens - 1.0)
        self._last_refill_at = now


# ---------------------------------------------------------------------------
# CachedReference：缓存装饰器（实现 MetadataReference）
# ---------------------------------------------------------------------------


class CachedReference:
    """给任意 ``MetadataReference`` 加剧目级缓存（可叠加频控）。

    流程：查缓存（命中且未过期 → 直接返回，含负缓存命中返回 ``None``，
    均不打 adapter 也不过频控）→ 未命中则（可选）频控后调上游 adapter →
    结果（含 ``None`` 的负缓存）写缓存 → 返回。``provider`` 是注册名
    （如 ``"bangumi"``），作为缓存键的一部分与 ``reference_order`` 对齐。
    缓存读写失败按 miss/跳过处理并记日志，不阻断参考链。
    """

    def __init__(
        self,
        *,
        provider: str,
        upstream: MetadataReference,
        store: ReferenceCacheStore,
        now_fn: Callable[[], datetime] = datetime.now,
        positive_ttl_s: float = DEFAULT_POSITIVE_TTL_S,
        negative_ttl_s: float = DEFAULT_NEGATIVE_TTL_S,
        limiter: TokenBucketLimiter | None = None,
    ) -> None:
        self._provider = provider
        self._upstream = upstream
        self._store = store
        self._now_fn = now_fn
        self._positive_ttl_s = positive_ttl_s
        self._negative_ttl_s = negative_ttl_s
        self._limiter = limiter

    async def aclose(self) -> None:
        """透传上游 adapter 的资源释放（如 httpx client）。"""
        close = getattr(self._upstream, "aclose", None)
        if close is not None:
            await close()

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        """MetadataReference 契约入口：先缓存，后频控 + 上游。"""
        hit, facts = await self._read_cache(title_shape)
        if hit:
            return facts
        if self._limiter is not None:
            await self._limiter.acquire()
        facts = await self._upstream.lookup(title_shape)
        await self._write_cache(title_shape, facts)
        return facts

    async def _read_cache(self, title_shape: str) -> tuple[bool, ReferenceFacts | None]:
        """读缓存；返回 ``(是否命中, 命中事实)``。

        - 正缓存命中且未过期 → ``(True, facts)``；
        - 负缓存命中且未过期 → ``(True, None)``（挡住对上游的调用）；
        - 未命中/已过期/读取失败/``facts`` 形状不符 → ``(False, None)``
          （继续走上游）。

        缓存读写失败一律按未命中处理并记日志，不阻断参考链。
        """
        try:
            row = await self._store.find_reference_cache(title_shape, self._provider)
        except Exception:
            logger.warning("reference cache read failed; treating as miss")
            return (False, None)
        if row is None:
            return (False, None)
        if not self._is_fresh(row):
            return (False, None)
        if is_negative_json(row.facts):
            return (True, None)
        facts = facts_from_json(row.facts)
        if facts is None:
            logger.warning("reference cache facts malformed; treating as miss")
            return (False, None)
        return (True, facts)

    async def _write_cache(self, title_shape: str, facts: ReferenceFacts | None) -> None:
        """写缓存（正/负统一入口）；失败只记日志，不影响返回值。"""
        now = self._now_fn()
        if facts is None:
            payload: dict[str, object] = {_NEGATIVE_KEY: True}
            ttl = self._negative_ttl_s
        else:
            payload = facts_to_json(facts)
            ttl = self._positive_ttl_s
        row = ReferenceCache(
            title_shape=title_shape,
            provider=self._provider,
            facts=payload,
            fetched_at=now,
            expires_at=_shift(now, ttl),
        )
        try:
            await self._store.add_reference_cache(row)
        except Exception:
            logger.warning("reference cache write failed; skipping")

    def _is_fresh(self, row: ReferenceCache) -> bool:
        if row.expires_at is None:
            return True
        return row.expires_at > self._now_fn()


def _shift(now: datetime, ttl_s: float) -> datetime:
    """``now + ttl``（naive 本地时间，与 ``fetched_at`` 同基准）。"""
    return now + timedelta(seconds=ttl_s)
