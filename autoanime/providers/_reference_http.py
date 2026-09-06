"""Bangumi/TMDB 参考源适配器共用基础设施。

三个职责（全部可离线注入测试）：

1. 频控与退避纯函数：``compute_rate_wait``（QPS 频控）、
   ``parse_retry_after``（429 退避秒数）——无状态、无 I/O；
2. ``ReferenceHttpClient``：两个 adapter 共用的 httpx 薄封装。transport /
   clock / sleeper 可注入（单测用 ``httpx.MockTransport`` + 零等待 fake）；
   请求间隔状态是实例状态，不是模块级全局（PR6 规则 5）；
3. 共享的标题匹配纯函数：``bare_query``（把 L2 title shape 折叠成检索
   关键词）、``normalize_title``、``pick_candidate``（在候选里选出命中
   条目）。

失败语义统一在 HTTP 层：网络错误/超时/HTTP 4xx/5xx/非法 JSON → 返回
``None``；429 按 ``Retry-After`` 退避一次，再失败即 ``None``。
本层异常不向上传播，异常文案不携带完整 URL 与密钥（PR6 规则 9）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC
from email.utils import parsedate_to_datetime

import httpx

DEFAULT_TIMEOUT_S = 10.0
"""单请求超时（秒），可通过构造参数覆盖。"""

DEFAULT_QPS = 1.0
"""默认每 provider 每秒最多 1 次请求（P2 的 DB 缓存与 QPS 策略另计）。"""

DEFAULT_RETRY_AFTER_S = 1.0
"""429 响应未带（或不可解析）``Retry-After`` 时的默认退避秒数。"""

MAX_RETRY_AFTER_S = 30.0
"""``Retry-After`` 退避秒数上限（防恶意大值拖死管线）。"""

SEASON_PLACEHOLDER = "{season}"
EPISODE_PLACEHOLDER = "{ep}"

SHORT_QUERY_MAX_LEN = 4
"""短 query 阈值：归一后长度 ≤ 此值的 query 不参与包含匹配（见 pick_candidate）。"""

# bare_query 里除含占位符的 token 外，还需丢弃的孤立季节/集锚点词
# （占位符与锚点词被空白隔开时的残留，如 "season {season}"）。
_ANCHOR_TOKENS = frozenset({"s", "ss", "season", "e", "ep", "eps", "episode"})


def compute_rate_wait(
    last_request_at: float | None, now: float, min_interval_s: float
) -> float:
    """QPS 频控纯函数：距下次允许请求还需等待的秒数（非负）。"""
    if min_interval_s <= 0.0 or last_request_at is None:
        return 0.0
    return max(0.0, min_interval_s - (now - last_request_at))


def parse_retry_after(value: str | None, *, default_s: float = DEFAULT_RETRY_AFTER_S) -> float:
    """解析 ``Retry-After``（秒数或 HTTP-Date），夹在 ``[default_s, MAX_RETRY_AFTER_S]``。

    任何解析失败都落到 ``default_s``；不抛异常。
    """
    if value is None:
        return default_s
    text = value.strip()
    if not text:
        return default_s
    seconds: float | None = None
    try:
        seconds = float(int(text))
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            seconds = when.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            seconds = None
    if seconds is None or seconds <= 0.0:
        return default_s
    return min(seconds, MAX_RETRY_AFTER_S)


def bare_query(title_shape: str) -> str:
    """把 L2 title shape 折叠成检索关键词（剧目级）。

    规则：丢弃含 ``{season}``/``{ep}`` 占位符的 token（季节/集锚点与数字
    同 token，如 ``第{season}季``、``s{season}e{ep}`` 一并丢弃），再丢弃
    孤立的锚点词残留，最后折叠空白。纯标题（无结构标记）只做空白折叠。
    """
    kept: list[str] = []
    for token in title_shape.split():
        if SEASON_PLACEHOLDER in token or EPISODE_PLACEHOLDER in token:
            continue
        if token.casefold() in _ANCHOR_TOKENS:
            continue
        kept.append(token)
    return " ".join(kept)


def normalize_title(text: str) -> str:
    """候选标题规范化：折叠空白 + casefold（与 shape 的 casefold 语义对齐）。"""
    return " ".join(text.split()).casefold()


def pick_candidate(
    candidates: Sequence[tuple[int, tuple[str, ...]]],
    query: str,
    *,
    min_containment_len: int = 5,
    short_query_max_len: int = SHORT_QUERY_MAX_LEN,
) -> int | None:
    """在候选里选出与 ``query`` 匹配的条目 id；无命中返回 ``None``。

    匹配规则（权威参考的保守语义，query 与候选名均先经 ``normalize_title``
    归一——空白折叠 + casefold）：
    1. 任一名字归一后与 query 完全相等 → 命中（优先）；
    2. 短 query 前缀放行：归一 query 长度 ≤ ``short_query_max_len`` 时，
       「query 是某名字前缀、且前缀之外的后缀不含任何文字内容（仅剩
       标点/符号/空白）」视为一次命中；命中条目**唯一**才放行，多于一个
       条目命中即拒绝（多义保护），零命中继续走包含回退；
    3. 包含回退（短 query 按上述保护跳过）：包含关系（query 在名字里，
       或名字在 query 里）且较短一边长度 ≥ ``min_containment_len`` →
       在命中者中取最短名字的候选。

    包含规则用于吸收查询侧残余后缀（如 "sword art online ii" 与
    "sword art online"）；短 query（含 4 字中文短名）默认不参与包含匹配，
    避免误命中外传/别名（如「刀剑神域」误配「刀剑神域外传」——后缀
    「外传」是文字内容，前缀放行同样不适用）。前缀放行只覆盖「唯一一个
    条目、后缀纯标点」的形态（如「孤独摇滚」→「孤独摇滚！」）。
    """
    normalized_query = normalize_title(query)
    if not normalized_query:
        return None
    # 1) 精确相等优先。
    for candidate_id, names in candidates:
        for name in names:
            if normalize_title(name) == normalized_query:
                return candidate_id
    # 2) 短 query：仅允许「前缀命中 + 后缀无文字内容 + 唯一条目」放行。
    if len(normalized_query) <= short_query_max_len:
        hits = _short_prefix_hits(candidates, normalized_query)
        if len(hits) > 1:
            return None  # 多义保护：多个条目命中，不替参考源做决定。
        if len(hits) == 1:
            return next(iter(hits))
        # 零命中：继续包含回退（默认参数下对短 query 必然无果，行为一致）。
    # 3) 包含回退：取最短命中名。
    best: tuple[int, str] | None = None
    for candidate_id, names in candidates:
        for name in names:
            normalized_name = normalize_title(name)
            if not normalized_name:
                continue
            shorter = min(len(normalized_query), len(normalized_name))
            if shorter < min_containment_len:
                continue
            contained = (
                normalized_name in normalized_query or normalized_query in normalized_name
            )
            if contained and (best is None or len(normalized_name) < len(best[1])):
                best = (candidate_id, normalized_name)
    return None if best is None else best[0]


def _short_prefix_hits(
    candidates: Sequence[tuple[int, tuple[str, ...]]], normalized_query: str
) -> set[int]:
    """短 query 的前缀命中条目集合：后缀只含标点/符号/空白才计入。"""
    hits: set[int] = set()
    for candidate_id, names in candidates:
        for name in names:
            normalized_name = normalize_title(name)
            if not normalized_name.startswith(normalized_query):
                continue
            suffix = normalized_name[len(normalized_query) :]
            # 后缀含任何文字内容（字母/数字/CJK 等 isalnum 字符）即视为
            # 语义延伸（外传/第二季…），不命中；纯标点后缀（如「！」）放行。
            if not any(ch.isalnum() for ch in suffix):
                hits.add(candidate_id)
                break
    return hits


class ReferenceHttpClient:
    """两个参考源 adapter 共用的请求执行器。

    - ``transport`` 可注入（单测 ``httpx.MockTransport``）；``None`` 时真实网络。
    - ``clock``/``sleeper`` 可注入（单测零等待）。
    - QPS 频控状态（``_last_request_at``）与底层 client 都是实例状态。
    - 失败语义：网络错误/超时/4xx/5xx/非法 JSON → ``None``；429 按
      ``Retry-After`` 退避一次再失败即 ``None``。绝不抛异常。
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        qps: float = DEFAULT_QPS,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock if clock is not None else time.monotonic
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._timeout = httpx.Timeout(timeout_s)
        self._min_interval_s = 1.0 / qps if qps > 0 else 0.0
        self._headers = dict(headers) if headers else {}
        self._last_request_at: float | None = None
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        """释放底层 client（adapter 生命周期结束时调用）。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object | None:
        """发一次（429 时最多两次）请求并解析 JSON；任何失败返回 ``None``。"""
        for attempt in (0, 1):
            await self._throttle()
            try:
                client = await self._ensure_client()
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers={**self._headers, **(headers or {})},
                )
            except httpx.HTTPError:
                return None
            if response.status_code == 429:
                if attempt == 0:
                    delay = parse_retry_after(response.headers.get("retry-after"))
                    await self._sleeper(delay)
                    continue
                return None
            if not (200 <= response.status_code < 300):
                return None
            try:
                return response.json()
            except (ValueError, TypeError):
                # 非 JSON（含 json.JSONDecodeError）：与请求失败同样语义。
                return None
        return None



    async def request_content(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bytes, str] | None:
        """发一次（429 时最多两次）请求并返回 ``(字节体, Content-Type)``。

        与 :meth:`request_json` 同一套失败语义：网络错误/超时/4xx/5xx →
        ``None``；429 按 ``Retry-After`` 退避一次再失败即 ``None``；绝不抛
        异常。供海报等二进制资源下载复用频控与超时（图片 CDN 直链不走
        API 配额，但节流仍在此层统一生效）。
        """
        for attempt in (0, 1):
            await self._throttle()
            try:
                client = await self._ensure_client()
                response = await client.request(
                    method,
                    url,
                    headers={**self._headers, **(headers or {})},
                )
            except httpx.HTTPError:
                return None
            if response.status_code == 429:
                if attempt == 0:
                    delay = parse_retry_after(response.headers.get("retry-after"))
                    await self._sleeper(delay)
                    continue
                return None
            if not (200 <= response.status_code < 300):
                return None
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            return response.content, content_type
        return None
    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,
            )
        return self._client

    async def _throttle(self) -> None:
        """QPS 频控：必要时等待，然后记录本次请求时间（实例状态）。"""
        now = self._clock()
        wait = compute_rate_wait(self._last_request_at, now, self._min_interval_s)
        if wait > 0.0:
            await self._sleeper(wait)
        self._last_request_at = self._clock()
