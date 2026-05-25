# -*- coding: utf-8 -*-
"""
Normalize ASCII punctuation to full-width Chinese punctuation in api_cache.json
for strings that contain CJK, excluding URL-like values and filename keys.

Run: python scripts/normalize_api_cache_cn_punct.py
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

CACHE_PATH = r"C:\Users\17645\Desktop\AutoAnime\.cache\api_cache.json"

# Basic CJK blocks (titles; not exhaustive CJK ext)
_HAN = re.compile(r"[\u4e00-\u9fff]")
_URL = re.compile(r"^https?://", re.I)
_VIDEO_EXT = re.compile(
    r"\.(mp4|mkv|ass|srt|avi|webm|flv|mov|m4v|mpg|mpeg|wmv)(\[[^\]]*\])?$", re.I
)


def has_han(s: str) -> bool:
    return _HAN.search(s) is not None


def is_filename_like_key(s: str) -> bool:
    if not isinstance(s, str) or s == "":
        return False
    sl = s.lower()
    if _VIDEO_EXT.search(sl):
        return True
    if "[" in s and "]" in s:
        return True
    if re.search(r"\.(mp4|mkv|ass|srt)\b", sl):
        return True
    return False


def ascii_double_to_curly(s: str) -> str:
    if '"' not in s:
        return s
    parts = s.split('"')
    out = [parts[0]]
    for i in range(1, len(parts)):
        q = "\u201c" if (i % 2 == 1) else "\u201d"
        out.append(q + parts[i])
    return "".join(out)


def ascii_single_to_curly(s: str) -> str:
    if "'" not in s:
        return s
    parts = s.split("'")
    out = [parts[0]]
    for i in range(1, len(parts)):
        q = "\u2018" if (i % 2 == 1) else "\u2019"
        out.append(q + parts[i])
    return "".join(out)


def normalize_cn_punct_text(s: str) -> str:
    if not s or not has_han(s):
        return s
    if _URL.match(s.strip()):
        return s

    t = s
    # Colon between CJK (not Re: style: Latin before colon)
    t = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff01-\uff60]):(?=[\u4e00-\u9fff])", "：", t)
    # Comma between CJK
    t = re.sub(r"(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])", "，", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff]),(\s+)(?=[\u4e00-\u9fff])", r"，\1", t)
    # Semicolon between CJK
    t = re.sub(r"(?<=[\u4e00-\u9fff]);(?=[\u4e00-\u9fff])", "；", t)
    # Exclamation / question adjacent to CJK
    t = re.sub(r"(?<=[\u4e00-\u9fff])!", "！", t)
    t = re.sub(r"!(?=[\u4e00-\u9fff])", "！", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\?", "？", t)
    t = re.sub(r"\?(?=[\u4e00-\u9fff])", "？", t)
    # Slash between CJK (e.g. 怀玉/涩谷)
    t = re.sub(r"(?<=[\u4e00-\u9fff])/(?=[\u4e00-\u9fff])", "／", t)
    # Parentheses touching CJK
    t = re.sub(r"(?<=[\u4e00-\u9fff])\(", "（", t)
    t = re.sub(r"\((?=[\u4e00-\u9fff])", "（", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\)", "）", t)
    t = re.sub(r"\)(?=[\u4e00-\u9fff])", "）", t)

    t = ascii_double_to_curly(t)
    t = ascii_single_to_curly(t)

    # Trailing period after CJK (line or string end)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=\s*$)", "。", t)

    return t


def merge_nodes(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        if "ts" in a and "ts" in b:
            ta, tb = float(a.get("ts", 0)), float(b.get("ts", 0))
            pick = a if ta >= tb else b
            out = dict(pick)
            out["ts"] = max(ta, tb)
            return out
        if "value" in a and "value" in b:
            va, vb = a["value"], b["value"]
            if isinstance(va, str) and isinstance(vb, str):
                out = dict(b)
                out["ts"] = max(float(a.get("ts", 0)), float(b.get("ts", 0)))
                return out
    return b


def normalize_key(k: str) -> str:
    if is_filename_like_key(k):
        return k
    if not has_han(k):
        return k
    return normalize_cn_punct_text(k)


def normalize_tree(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict[Any, Any] = {}
        for k, v in obj.items():
            nk = normalize_key(k) if isinstance(k, str) else k
            nv = normalize_tree(v)
            if nk in out:
                out[nk] = merge_nodes(out[nk], nv)
            else:
                out[nk] = nv
        return out
    if isinstance(obj, list):
        return [normalize_tree(x) for x in obj]
    if isinstance(obj, str):
        return normalize_cn_punct_text(obj)
    return obj


def main() -> Tuple[int, int]:
    with open(CACHE_PATH, encoding="utf-8") as f:
        raw = f.read()
    before = json.loads(raw)
    after = normalize_tree(before)
    bs = json.dumps(before, ensure_ascii=False, sort_keys=True)
    as_ = json.dumps(after, ensure_ascii=False, sort_keys=True)
    changed = 0 if bs == as_ else 1
    with open(CACHE_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(after, f, ensure_ascii=False, indent=2)
        f.write("\n")
    # count string diffs roughly
    n_diff = sum(1 for x, y in zip(bs.splitlines(), as_.splitlines()) if x != y) if changed else 0
    print("cache updated:", CACHE_PATH)
    print("structural change:", bool(changed), "line_diff_approx:", n_diff)
    return changed, n_diff


if __name__ == "__main__":
    main()
