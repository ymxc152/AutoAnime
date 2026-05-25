"""
JSONL 审计：alias / canonical 写入、拒绝
"""

import json
from time import time
from uuid import uuid4

from ..logging_utils import Auxiliary_Log


def Auxiliary_AppendPollutionAudit(event_type: str, detail: dict) -> None:
    """
    向 `.cache/pollution_audit.jsonl` 追加一行 JSON。
    detail 中可含 alias_key, canonical_id, reason, source 等；自动补充 ts、audit_id。
    """
    from .persistent import Auxiliary_GetCacheDir

    line = {
        "audit_id": str(uuid4()),
        "ts": time(),
        "type": str(event_type),
    }
    if type(detail) is dict:
        line.update(detail)
    p = Auxiliary_GetCacheDir() / "pollution_audit.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as err:
        Auxiliary_Log(f"pollution_audit 写入失败: {err}", "WARNING")
