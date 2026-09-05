"""Text normalization shared by every L1 dialect recognizer.

These helpers only prepare a raw name for anchor scanning: they strip the
container extension, fold whitespace/fullwidth punctuation, and drop common
noise segments (recruitment brackets, site watermarks). Dialect-specific
reconstruction (e.g. word-internal dots) happens in the dialect modules on
top of these primitives.
"""

from __future__ import annotations

import re
import unicodedata

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mkv",
        ".mp4",
        ".m2ts",
        ".ts",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".ogm",
        ".ass",
        ".ssa",
        ".srt",
    }
)

_EXTENSION_RE = re.compile(r"\.(?P<ext>[A-Za-z0-9]{1,5})$")
_DOT_LIKE_SEPARATOR_RE = re.compile(r"(?<!\d)[._＿．]|[._＿．](?!\d)")
_NOISE_BRACKET_RE = re.compile(
    r"[\[【][^\]】]{0,60}"
    r"(?:招募|招新|招聘|宣传|寻求|合作|应援|订阅|分享|更新|网址)"
    r"[^\]】]{0,60}[\]】]"
)
_BRACKETED_DOMAIN_RE = re.compile(r"[\[【][^\]】]*\.(?:com|net|org|cc|me|xyz|tv)[^\]】]*[\]】]")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def strip_extension(name: str) -> str:
    """Remove a trailing known media extension; unknown suffixes are kept."""
    match = _EXTENSION_RE.search(name)
    if match is None:
        return name.strip()
    if f".{match.group('ext').lower()}" in VIDEO_EXTENSIONS:
        return name[: match.start()].strip()
    return name.strip()


def normalize_whitespace(text: str) -> str:
    """NFC-fold the text and collapse every whitespace run into one space."""
    folded = unicodedata.normalize("NFC", text).replace("\u3000", " ")
    return re.sub(r"\s+", " ", folded).strip()


def separators_to_spaces(text: str) -> str:
    """Turn dot/underscore separators into spaces, keeping decimals intact."""
    return normalize_whitespace(_DOT_LIKE_SEPARATOR_RE.sub(" ", text))


def clean_noise(text: str) -> str:
    """Drop recruitment brackets, bracketed domains, and bare URLs."""
    text = _NOISE_BRACKET_RE.sub(" ", text)
    text = _BRACKETED_DOMAIN_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    return normalize_whitespace(text)


def normalize_name(name: str) -> str:
    """Full preprocessing pipeline: extension, noise, whitespace."""
    return clean_noise(strip_extension(name))
