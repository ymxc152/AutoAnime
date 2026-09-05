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

纯函数，无 I/O、无模块级可变状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from autoanime.core.enums import Segment

L3_EVIDENCE = "llm"
L3_FIELDS: tuple[str, ...] = ("title", "season", "episode", "segment", "fansub")

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
