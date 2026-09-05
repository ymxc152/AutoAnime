"""L3 prompt 构建（纯函数，模板内不含任何密钥或环境信息）。

- ``build_prompt``：首轮 prompt——raw release name + 可选 L1 结果提示 +
  可选上下文提示 + 固定的严格 schema 说明；
- ``build_correction_prompt``：schema 违规后的纠正重试 prompt——回放
  原始 prompt、贴出无效响应与违规原因、重申 schema。

模板是静态常量；同样的输入永远得到同样的输出。
"""

from __future__ import annotations

from autoanime.core.interfaces import ParseContext, ParseResult

_SCHEMA_BLOCK = """Respond with ONLY a JSON object with exactly these fields:
- "title": non-empty string
- "season": integer or null
- "episode": integer or null
- "segment": one of episode / season_pack / movie
- "fansub": non-empty string or null

Do not add any field outside this list. Do not wrap the JSON in markdown."""

_PROMPT_TEMPLATE = f"""You parse anime release names into structured metadata.

Release name: {{raw_name}}
{{hint_block}}

{{context_block}}

{_SCHEMA_BLOCK}"""

_CORRECTION_TEMPLATE = f"""Your previous reply violated the required schema.

Problem: {{reason}}

Invalid reply:
{{invalid_response}}

Original task:
{{original_prompt}}

{_SCHEMA_BLOCK}"""


def build_prompt(
    raw_name: str,
    l1_result: ParseResult | None,
    context: ParseContext | None,
) -> str:
    """首轮 prompt：raw name + L1 提示 + 上下文提示。"""
    return _PROMPT_TEMPLATE.format(
        raw_name=raw_name,
        hint_block=_hint_block(l1_result),
        context_block=_context_block(context),
    )


def build_correction_prompt(
    previous_prompt: str, invalid_response: str, reason: str
) -> str:
    """纠正重试 prompt：原 prompt + 无效响应 + 违规原因 + schema 重申。"""
    return _CORRECTION_TEMPLATE.format(
        reason=reason,
        invalid_response=invalid_response,
        original_prompt=previous_prompt,
    )


def _hint_block(l1_result: ParseResult | None) -> str:
    if l1_result is None:
        return "Local parsing produced no result."
    parts = [
        f"title={l1_result.title}",
        f"season={l1_result.season}",
        f"episode={l1_result.episode}",
        f"segment={l1_result.segment.value}",
        f"fansub={l1_result.fansub}",
    ]
    return "Local parse hint (may be wrong or incomplete): " + ", ".join(parts)


def _context_block(context: ParseContext | None) -> str:
    if context is None:
        return "No extra context."
    parts: list[str] = []
    if context.known_series is not None:
        parts.append(f"known series id: {context.known_series}")
    if context.release_progress is not None:
        parts.append(f"latest released episode: {context.release_progress}")
    if context.fansub_pref is not None:
        parts.append(f"preferred fansub: {context.fansub_pref}")
    if not parts:
        return "No extra context."
    return "Context: " + "; ".join(parts) + "."
