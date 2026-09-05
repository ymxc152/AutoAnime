"""llm_cache 的 key 规范化与缓存读写语义（纯函数侧）。

llm_cache 与 bypass_list 共用同一套 pattern 规范化：L1 name 规范化
（去扩展名/噪声/空白）+ 分隔符折叠 + casefold，摘要用同一个稳定
SHA-256（``l2.keys.stable_hash``）。因此同一个 raw release name 在
bypass 与 llm_cache 两张表里的键完全一致。

读写语义（PR5 契约，由 T2 识别器与 T3 transport 遵守）：

- 读：任何真实 LLM 调用之前先 ``get``；命中则把录制的响应回放同一套
  严格 schema 解析——缓存响应解析失败的，按缓存未命中处理并继续真实
  调用（防脏数据永久污染）；
- 写：只有真实调用成功（schema 合法）的响应才 ``put``；失败与非法
  响应一律不落缓存；
- 每个 raw_name 每轮最多一次真实调用（重试除外）。

DB 会话只存在于 store 层（T2）；本模块只定义键与 ``LlmCache`` 结构。
"""

from __future__ import annotations

from dataclasses import dataclass

from autoanime.pipeline.l2.bypass import normalize_pattern, pattern_hash


def llm_pattern(raw_name: str) -> str:
    """raw release name 的规范化 pattern 文本（与 bypass 一致）。"""
    return normalize_pattern(raw_name)


def llm_cache_key(raw_name: str) -> str:
    """llm_cache 的键：规范化 pattern 的稳定摘要（与 bypass 同源）。"""
    return pattern_hash(raw_name)


@dataclass(frozen=True)
class LlmCache:
    """llm_cache 中一条录制记录。

    ``response`` 是录制的模型输出文本；回放时走与真实调用相同的严格
    schema 解析。``model`` 记录产出该响应的模型名，仅供审计参考。
    """

    pattern_hash: str
    response: str
    model: str | None = None
