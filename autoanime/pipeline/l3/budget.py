"""超时 / 重试 / 预算的判定纯函数与常量（PR5 统一契约）。

- 超时 10s（真实计时在 transport/provider 层，这里只定常量与判定）；
- 网络 / 超时类错误重试上限 ``LLM_MAX_RETRIES``（默认 2，可配置上限仍
  以 config 为准——本模块是默认契约值）；
- schema 违规走纠正重试，上限 ``LLM_SCHEMA_CORRECTION_RETRIES``（1 次），
  再失败放弃并计数；
- 预算默认不限（``budget=None``）；超限**只记 audit 不阻断**——判定函数
  只回答「是否已超限」，绝不拦截调用。
"""

from __future__ import annotations

LLM_TIMEOUT_S: float = 10.0
LLM_MAX_RETRIES: int = 2
LLM_SCHEMA_CORRECTION_RETRIES: int = 1


def transport_retry_allowed(failed_attempts: int) -> bool:
    """网络/超时失败 ``failed_attempts`` 次后是否还允许重试（上限 2）。"""
    return failed_attempts < LLM_MAX_RETRIES


def schema_correction_allowed(corrections_made: int) -> bool:
    """已做 ``corrections_made`` 次纠正重试后是否还允许再来一次（上限 1）。"""
    return corrections_made < LLM_SCHEMA_CORRECTION_RETRIES


def budget_exceeded(calls_used: int, budget: int | None) -> bool:
    """``calls_used`` 是否已超出 ``budget``；``None`` = 不限。

    超限只用于记 audit，调用方不得据此阻断 LLM 调用。
    """
    return budget is not None and calls_used > budget
