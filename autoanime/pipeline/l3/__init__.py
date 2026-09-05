"""L3 元数据层（LLM fallback）的公共契约与纯函数基础设施（PR5 T1）。

L3 是增强层：LLM 草稿 + 参考源事实进 arbiter 仲裁（T4），仲裁失败/
不可用时交回 orchestrator 按 L1/L2 原结果路由。模块：

- schema:    LLM 响应严格解析（字段白名单、类型校验）与 L3Draft
- prompt:    首轮与纠正重试 prompt 构建（纯文本，无密钥）
- budget:    超时/重试/预算的判定纯函数与常量
- cache_key: llm_cache 键规范化（与 bypass 同源）与 LlmCache 结构
- draft:     L3Draft → ParseResult 构建（evidence="llm"，name/folder 保护）
- reference: ReferenceFacts 结构与 ReferenceChain 组合器
- arbiter:   仲裁决策表签名（T1 定，T4 实现）

网络调用只在 LlmTransport 实现（T3）；DB 会话只在 LlmCacheStore 实现
（T2）；外部参考源 provider 注册进 Registry，纯函数组件不进 registry。
"""

from autoanime.pipeline.l3.arbiter import (
    EVIDENCE_PRIORITY,
    ArbiterAudit,
    ArbiterInput,
    ArbiterVerdict,
    FieldResolution,
    arbitrate,
    disambiguate_season,
    evidence_rank,
    resolve_field,
    title_shape_matches,
    upgrade_level,
)
from autoanime.pipeline.l3.budget import (
    LLM_MAX_RETRIES,
    LLM_SCHEMA_CORRECTION_RETRIES,
    LLM_TIMEOUT_S,
    budget_exceeded,
    schema_correction_allowed,
    transport_retry_allowed,
)
from autoanime.pipeline.l3.cache_key import LlmCache, llm_cache_key, llm_pattern
from autoanime.pipeline.l3.draft import apply_l3_draft, l3_parse_result
from autoanime.pipeline.l3.prompt import (
    batch_release_names_from_prompt,
    build_batch_prompt,
    build_correction_prompt,
    build_prompt,
)
from autoanime.pipeline.l3.reference import ReferenceChain, ReferenceFacts
from autoanime.pipeline.l3.schema import (
    BATCH_FIELDS,
    BATCH_INDEX_FIELD,
    L3_EVIDENCE,
    L3_FIELDS,
    REASON_MISSING_FIELD,
    REASON_NOT_JSON,
    REASON_TYPE_ERROR,
    REASON_UNKNOWN_FIELD,
    L3Draft,
    LlmResponseError,
    parse_batch_response,
    parse_llm_payload,
    parse_llm_response,
)

__all__ = [
    "BATCH_FIELDS",
    "BATCH_INDEX_FIELD",
    "L3_EVIDENCE",
    "L3_FIELDS",
    "LLM_MAX_RETRIES",
    "LLM_SCHEMA_CORRECTION_RETRIES",
    "LLM_TIMEOUT_S",
    "REASON_MISSING_FIELD",
    "REASON_NOT_JSON",
    "REASON_TYPE_ERROR",
    "REASON_UNKNOWN_FIELD",
    "ArbiterAudit",
    "ArbiterInput",
    "ArbiterVerdict",
    "EVIDENCE_PRIORITY",
    "FieldResolution",
    "L3Draft",
    "LlmCache",
    "LlmResponseError",
    "ReferenceChain",
    "ReferenceFacts",
    "apply_l3_draft",
    "arbitrate",
    "batch_release_names_from_prompt",
    "budget_exceeded",
    "build_batch_prompt",
    "build_correction_prompt",
    "build_prompt",
    "disambiguate_season",
    "evidence_rank",
    "l3_parse_result",
    "llm_cache_key",
    "llm_pattern",
    "parse_batch_response",
    "parse_llm_payload",
    "parse_llm_response",
    "resolve_field",
    "schema_correction_allowed",
    "title_shape_matches",
    "transport_retry_allowed",
    "upgrade_level",
]
