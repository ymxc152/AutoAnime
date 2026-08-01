from __future__ import annotations

import re
import unicodedata
import json
from pathlib import Path
from typing import Iterable, List


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_SEPARATORS = re.compile(r"[._\-]+")
_ZHCONV_READY = False


def _init_zhconv_safely() -> None:
    global _ZHCONV_READY
    if _ZHCONV_READY:
        return
    _ZHCONV_READY = True
    try:
        import zhconv.zhconv as module

        if getattr(module, "zhcdicts", None) is not None:
            return
        dictionary = getattr(module, "DICTIONARY", "zhcdict.json")
        raw = b""
        if dictionary == getattr(module, "_DEFAULT_DICT", "zhcdict.json"):
            try:
                from importlib.resources import open_binary

                with open_binary("zhconv", dictionary) as handle:
                    raw = handle.read()
            except Exception:
                stream = module.get_module_res(dictionary)
                try:
                    raw = stream.read()
                finally:
                    if hasattr(stream, "close"):
                        stream.close()
        else:
            with open(dictionary, "rb") as handle:
                raw = handle.read()
        data = json.loads(raw.decode("utf-8"))
        data["SIMPONLY"] = frozenset(data.get("SIMPONLY", []))
        data["TRADONLY"] = frozenset(data.get("TRADONLY", []))
        module.zhcdicts = data
    except Exception:
        return


def to_simplified(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    try:
        _init_zhconv_safely()
        from zhconv import convert

        return convert(text, "zh-cn")
    except Exception:
        return text


def contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(str(value or "")))


def cjk_count(value: str) -> int:
    return len(_CJK_RE.findall(str(value or "")))


def alias_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", to_simplified(value)).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"(?:complete|全集|全\s*\d+\s*[集話话])", "", text, flags=re.I)
    text = re.sub(r"\b(?:19|20)\d{2}\b", "", text)
    text = re.sub(r"\b(?:s(?:eason)?\s*)?0*([1-9]\d*)\s*(?:st|nd|rd|th)?\s*season\b", "", text, flags=re.I)
    text = re.sub(r"\bs0*([1-9]\d*)\b", "", text, flags=re.I)
    return "".join(ch for ch in text if ch.isalnum() or "\u3400" <= ch <= "\u9fff")


def display_title(value: str) -> str:
    text = to_simplified(value)
    text = _SEPARATORS.sub(" ", text)
    text = _SPACES.sub(" ", text).strip(" ._-[]【】()（）")
    return text


def strip_season_markers(value: str) -> str:
    text = display_title(value)
    text = re.sub(r"\s*第\s*[一二三四五六七八九十百\d]+\s*季", " ", text)
    text = re.sub(r"\s*(?:FINAL|\d+(?:st|nd|rd|th))\s+SEASON\b", " ", text, flags=re.I)
    text = re.sub(r"\s+S(?:eason)?\s*0*\d+\b", " ", text, flags=re.I)
    return _SPACES.sub(" ", text).strip(" ._-")


def safe_component(value: str, max_length: int = 120) -> str:
    text = to_simplified(value).strip()
    text = _INVALID_WINDOWS.sub(" ", text)
    text = _SPACES.sub(" ", text).strip(" .")
    if not text:
        text = "Unknown"
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update("COM%d" % i for i in range(1, 10))
    reserved.update("LPT%d" % i for i in range(1, 10))
    if text.split(".", 1)[0].upper() in reserved:
        text = "_" + text
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    return text


def unique_nonempty(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = display_title(value)
        key = alias_key(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except (OSError, ValueError):
        return False
