"""
alias 写入信任等级与校验（Schema v2）

- `Auxiliary_TrustLevelFromSource` 由来源推默认 trust
- `Auxiliary_ValidateAliasWrite` 写入前校验，失败时仅拒绝不落盘
"""

import re
from typing import Any, Tuple

from .. import state
from ..text_utils import (
    Auxiliary_HasChineseText,
    Auxiliary_NormalizeApiTitle,
    Auxiliary_NormalizeDisplayTitle,
)

# 归一化别名键最大长度；超过则拒绝写入 TitleAliasIndex（审计 reason=alias_key_too_long）
ALIAS_KEY_MAX_LEN = 100


def Auxiliary_TrustLevelFromSource(
    source_tag: str, *, conflict: bool = False, openai_downgrade: bool = False
) -> int:
    """
    与 docs/plan 中 trust 等级对齐的默认值（自动写入路径）。
    """
    t = "" if source_tag in [None, ""] else str(source_tag)
    t_lower = t.lower()
    if conflict and ("openai" in t_lower or t in ["OpenAI", "openai_identify"]):
        return 40
    if openai_downgrade and ("openai" in t_lower or t in ["openai_identify", "OpenAI"]):
        return 40
    if t in ("manual", "manual_title_whitelist", "ManualWhitelist"):
        return 100
    if t in ("BGM",):
        return 90
    if t in ("Bangumi", "TMDB"):
        return 80
    if t in ("openai_identify", "OpenAI"):
        return 60
    if t in ("unknown", "legacy", ""):
        return 40
    return 45


def _existing_alias_trust(entry: Any) -> int:
    if type(entry) is not dict:
        return 0
    try:
        return int(entry.get("trust_level", 0) or 0)
    except Exception:
        return 0


def _canonical_bare_titles(canonical_id: str) -> bool:
    """
    对应「canonicals 中无任何可用主名」：zh/en/romaji 全空则拒绝 alias。
    """
    from .canonical import Auxiliary_GetCanonicalTitleRecord

    rec = Auxiliary_GetCanonicalTitleRecord(str(canonical_id))
    if type(rec) is not dict:
        return True
    zh = Auxiliary_NormalizeApiTitle(str(rec.get("zh", "")))
    en = Auxiliary_NormalizeDisplayTitle(str(rec.get("en", "")))
    rj = Auxiliary_NormalizeDisplayTitle(str(rec.get("romaji", "")))
    return zh in [None, ""] and en in [None, ""] and rj in [None, ""]


def Auxiliary_ValidateAliasWrite(
    alias_key: str,
    canonical_id: str,
    trust_level: int,
    *,
    new_source: str = "",
) -> Tuple[bool, str]:
    """
    返回 (allow, reason)。allow False 时 reason 为可读原因，供审计。
    """
    if alias_key in [None, ""] or canonical_id in [None, ""]:
        return False, "empty_alias_or_canonical"
    if len(str(alias_key)) > ALIAS_KEY_MAX_LEN:
        return False, "alias_key_too_long"
    ak = str(alias_key)
    if re.fullmatch(r"\d+", ak) is not None:
        return False, "alias_pure_digits"
    if re.search(r"\d{4,}", ak) is not None:
        return False, "alias_digit_noise"
    if _canonical_bare_titles(str(canonical_id)):
        return False, "canonical_titles_empty"
    from .canonical import Auxiliary_GetCanonicalTitleRecord

    rec = Auxiliary_GetCanonicalTitleRecord(str(canonical_id))
    if type(rec) is dict and bool(rec.get("locked")) is True and int(trust_level) < 100:
        return False, "canonical_locked"
    g = state.PersistentApiCache.get("TitleAliasIndex", {}) if type(state.PersistentApiCache) is dict else {}
    ex = g.get(ak) if type(g) is dict else None
    if type(ex) is dict:
        et = _existing_alias_trust(ex)
        ex_cid = ex.get("value")
        if ex_cid in [None, ""]:
            ex_cid = ex.get("canonical_id")
    else:
        # 旧 v1 仅存 canonical id 串，无 trust
        et = 50
        ex_cid = ex
    if ex is None:
        return True, ""
    if int(et) > int(trust_level):
        if str(ex_cid) == str(canonical_id):
            return False, "same_canonical_higher_trust_noop"
        return False, "existing_higher_trust"
    return True, ""
