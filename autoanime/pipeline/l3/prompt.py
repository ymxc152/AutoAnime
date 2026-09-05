"""L3 prompt 构建（纯函数，模板内不含任何密钥或环境信息）。

- ``build_prompt``：首轮 prompt——raw release name + 可选 L1 结果提示 +
  可选上下文提示 + 固定的严格 schema 说明；
- ``build_correction_prompt``：schema 违规后的纠正重试 prompt——回放
  原始 prompt、贴出无效响应与违规原因、重申 schema；
- ``build_batch_prompt``（E1，9.3b 机会主义合批）：批量 prompt——多个
  release name 逐行列出（``Release name {i}: {name}``，可机械提取）+
  数组化 schema（每项多 ``index`` 对齐字段）+ 可选已知字幕组/上下文
  注入；``batch_release_names_from_prompt`` 从批量 prompt 机械提取
  release names（fake transport 与测试用）。

模板是静态常量；同样的输入永远得到同样的输出。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

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

_BATCH_SCHEMA_BLOCK = """Respond with ONLY a JSON array. Each element is an object with exactly these fields:
- "index": integer, the position number of the release name this answer belongs to
- "title": non-empty string
- "season": integer or null
- "episode": integer or null
- "segment": one of episode / season_pack / movie
- "fansub": non-empty string or null

Do not add any field outside this list. Do not wrap the JSON in markdown."""

_BATCH_PROMPT_TEMPLATE = f"""You parse anime release names into structured metadata.

Parse each of the following release names separately. They come from the same directory and the same fansub group.

{{name_block}}

{{fansub_block}}
{{context_block}}

{_BATCH_SCHEMA_BLOCK}"""

_BATCH_NAME_LINE_RE = re.compile(r"^Release name (\d+): (.+)$", re.MULTILINE)


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


def build_batch_prompt(
    raw_names: Sequence[str],
    *,
    fansub: str | None = None,
    context: ParseContext | None = None,
) -> str:
    """批量首轮 prompt（E1 合批）：逐行列出 release names + 数组化 schema。

    批内同目录同字幕组（合批键），已知字幕组作为上下文注入压缩幻觉空间
    （9.3b「上下文注入」的 v1 子集：fansub + ParseContext；top-5 别名与
    集数约束需要 series 级映射，进 backlog）。
    """
    name_block = "\n".join(
        f"Release name {position}: {name}" for position, name in enumerate(raw_names)
    )
    fansub_block = f"Known fansub for all names: {fansub}" if fansub else ""
    return _BATCH_PROMPT_TEMPLATE.format(
        name_block=name_block,
        fansub_block=fansub_block,
        context_block=_context_block(context),
    )


def batch_release_names_from_prompt(prompt: str) -> list[str]:
    """从批量 prompt 机械提取 release names（按 index 排序；fake/测试用）。

    单文件 prompt（``Release name: {name}``，无序号）不匹配批量行模式，
    返回空列表——两种 prompt 形态可据此区分。
    """
    matches = [
        (int(match.group(1)), match.group(2).strip())
        for match in _BATCH_NAME_LINE_RE.finditer(prompt)
    ]
    return [name for _, name in sorted(matches, key=lambda pair: pair[0])]
