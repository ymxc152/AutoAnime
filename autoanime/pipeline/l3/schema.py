"""LLM 响应严格 schema 解析与 L3Draft 结构（纯函数）。

严格 schema（PR5 统一契约）：JSON 对象，字段白名单
``title: str`` / ``season: int?`` / ``episode: int?`` / ``segment: enum`` /
``fansub: str?``。

- ``title`` 与 ``segment`` 必填；``season`` / ``episode`` / ``fansub``
  可缺省或为 null（缺省视作未知，不视为失败）；
- 任何违规——非 JSON、越界字段、类型错误、必填字段缺失——抛
  ``LlmResponseError``；纠正重试语义（带纠正提示重试 1 次，再失败放弃
  并计数）由调用方持有；
- 解析不做语义校验（负数 season、未来日期等留给 arbiter/参考源），
  也不做 markdown 围栏剥离：录制与缓存的都是纯 JSON 文本。

批量响应解析（E1，ARCHITECTURE 9.3b 数组输出）：``parse_batch_response``
把一个 JSON 数组解析成与批内 release name 数量对齐的草稿列表，每项多一
个 ``index`` 对齐字段。整体非法（非 JSON/非数组）→ 全 ``None``；项级
违规 → 该项 ``None``、其余照常；同一 ``index`` 出现多次 → 该 index 的
全部项置 ``None``（无法归属的输出宁可丢弃，由调用方走单文件重试兜底，
即「逐项校验失败不连坐」的解析侧语义）。该函数永不抛、永不返回 ``None``。

纯函数，无 I/O、无模块级可变状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from autoanime.core.enums import Segment

L3_EVIDENCE = "llm"
L3_FIELDS: tuple[str, ...] = ("title", "season", "episode", "segment", "fansub")

#: 批量响应（E1，9.3b 数组输出）每项在单文件白名单上多一个对齐字段。
BATCH_INDEX_FIELD = "index"
BATCH_FIELDS: tuple[str, ...] = (BATCH_INDEX_FIELD, *L3_FIELDS)

REASON_NOT_JSON = "not_json"
REASON_UNKNOWN_FIELD = "unknown_field"
REASON_TYPE_ERROR = "type_error"
REASON_MISSING_FIELD = "missing_field"


@dataclass(frozen=True)
class L3Draft:
    """一次 LLM 输出的结构化草稿（字段白名单内，evidence 恒为 ``llm``）。

    ``title`` / ``segment`` 为必填（schema 保证），其余字段缺省即未知。
    """

    title: str
    segment: Segment
    season: int | None = None
    episode: int | None = None
    fansub: str | None = None


class LlmResponseError(ValueError):
    """录制的 LLM 响应违反严格 schema。

    ``reason`` 是稳定的原因码（REASON_* 常量），供纠正提示与审计使用。
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        message = f"{reason}: {detail}" if detail else reason
        super().__init__(message)


def parse_llm_response(text: str) -> L3Draft:
    """把一段 LLM 输出文本解析成 L3Draft；违规抛 ``LlmResponseError``。"""
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise LlmResponseError(REASON_NOT_JSON, str(exc)) from exc
    if not isinstance(payload, dict):
        raise LlmResponseError(REASON_NOT_JSON, "response is not a JSON object")
    return parse_llm_payload(payload)


def parse_llm_payload(payload: dict[str, object]) -> L3Draft:
    """单个响应对象的白名单校验（单文件与批量响应共用的解析核心）。"""
    unknown = sorted(set(payload) - set(L3_FIELDS))
    if unknown:
        raise LlmResponseError(REASON_UNKNOWN_FIELD, ", ".join(unknown))

    title = payload.get("title")
    if title is None:
        raise LlmResponseError(REASON_MISSING_FIELD, "title")
    if not isinstance(title, str) or not title.strip():
        raise LlmResponseError(REASON_TYPE_ERROR, "title must be a non-empty string")

    season = _optional_int(payload, "season")
    episode = _optional_int(payload, "episode")
    segment = _required_segment(payload)
    fansub = _optional_fansub(payload)

    return L3Draft(
        title=title,
        segment=segment,
        season=season,
        episode=episode,
        fansub=fansub,
    )


def parse_batch_response(text: str, count: int) -> list[L3Draft | None]:
    """把批量响应解析成与批内条数对齐的草稿列表（永不抛、永不返回 ``None``）。

    - 整体非 JSON / 非数组：全 ``None``（调用方对整批逐项走单文件重试）；
    - 每项先校验 ``index``（整数、0 <= index < count），再把该项交给单
      文件白名单校验（``index`` 之外的字段集与单文件完全一致）；任何一
      步违规只置该项为 ``None``，不连坐；
    - 同一 ``index`` 被声明多次：该 index 的全部项都置 ``None``（无法
      归属的输出宁可丢弃）；
    - 缺失的 index 保持 ``None``。
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return [None] * count
    if not isinstance(payload, list):
        return [None] * count

    drafts: list[L3Draft | None] = [None] * count
    seen: dict[int, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        index = item.get(BATCH_INDEX_FIELD)
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not 0 <= index < count:
            continue
        seen[index] = seen.get(index, 0) + 1
        try:
            rest = {key: value for key, value in item.items() if key != BATCH_INDEX_FIELD}
            drafts[index] = parse_llm_payload(rest)
        except LlmResponseError:
            drafts[index] = None
    for index, occurrences in seen.items():
        if occurrences > 1:
            drafts[index] = None
    return drafts


def _optional_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LlmResponseError(REASON_TYPE_ERROR, f"{key} must be an integer or null")
    return value


def _required_segment(payload: dict[str, object]) -> Segment:
    value = payload.get("segment")
    if value is None:
        raise LlmResponseError(REASON_MISSING_FIELD, "segment")
    if isinstance(value, Segment):
        return value
    if isinstance(value, str):
        try:
            return Segment(value)
        except ValueError:
            pass
    raise LlmResponseError(REASON_TYPE_ERROR, f"segment must be one of {sorted(s.value for s in Segment)}")


def _optional_fansub(payload: dict[str, object]) -> str | None:
    value = payload.get("fansub")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LlmResponseError(REASON_TYPE_ERROR, "fansub must be a non-empty string or null")
    return value
