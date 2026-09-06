"""Dialect C: pure bracket-flow release names.

Dialect shape: ``[Group][Title][EP][1920x1080][AVC_AAC][CHT]`` with no
separators between brackets. Recognized traits:

- the fansub group sits in the first bracket (positional rule);
- no season marker: dialect C names are episode-only, so ``season`` stays
  absent and the draft keeps its missing-season MEDIUM grade;
- ``1920x1080`` style resolutions (no ``p`` suffix) and ``AVC_AAC`` style
  codec/language tokens are technical brackets, never fansub or title;
- the episode is the first purely-numeric bracket.

anitopy is only used as a title fallback and cross-check; every structural
field comes from the bracket positions.
"""

from __future__ import annotations

import re
from dataclasses import replace

from autoanime.core.enums import Segment
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import AnchorKind, find_anchors_of_kind
from autoanime.pipeline.l1.anitopy_adapter import parse_with_anitopy
from autoanime.pipeline.l1.confidence import base_level, downgrade, missing_fields_for
from autoanime.pipeline.l1.context import (
    SOURCE_CONTEXT,
    SOURCE_NAME,
    SOURCE_NONE,
    apply_release_progress,
    merge_folder_draft,
)
from autoanime.pipeline.l1.dialects.cjk import season_spans as cjk_season_spans
from autoanime.pipeline.l1.draft import L1Draft
from autoanime.pipeline.l1.normalize import (
    normalize_name,
    normalize_whitespace,
    separators_to_spaces,
)

# Technical bracket bodies: resolutions (1920x1080), episode numbers and
# ranges, codec/audio/subtitle/language tokens. Fansub names such as
# ``64bitsub`` deliberately do NOT full-match (trailing letters).
_TECH_INNER_RE = re.compile(
    r"(?:"
    r"\d{3,4}\s*[xX×]\s*\d{3,4}"
    r"|\d{1,4}(?:\s*[-~]\s*\d{1,4})?"
    r"|[xXhH]\.?26[45]"
    r"|HEVC|AVC|AV1|AAC|FLAC|MP3|MP4|ASS|SSA|SRT|Hi10P?"
    r"|\d{1,2}bit"
    r"|BD-?(?:Rip|Remux)|DVDRip|WEBRip|WEB-?DL|HDTV|REMUX|DDP?|Atmos|TrueHD|Opus|VSR"
    r"|E?AC-?3|DD[+P]?|WEB|DL"
    r"|\d{3,4}[pPiI]"
    r"|CHS|CHT|GBR|BIG5|繁|简"
    r")(?:_[A-Za-z0-9]+)*",
    re.IGNORECASE,
)


_ORDINAL_SEASON_RE = re.compile(
    r"(?P<num>\d{1,2})\s*(?:st|nd|rd|th)\s*(?:Season|季)",
    re.IGNORECASE,
)

# 括号内的字幕语言/版本标签（"简繁日内封" "TV版&无修版" "双语"）：含中文字样
# 即判定为标签，不作为标题/字幕组候选（dmhy 实测语料 F 轮缺陷）。
_LABEL_RE = re.compile(r"简|繁|日|内[嵌封]|双语|雙語|中字|TV版|无修|未删减|外[挂掛]")


def _season_matches(text: str) -> list[tuple[int, int, int]]:
    """Every season marker as ``(start, end, value)`` in positional order."""
    spans = list(cjk_season_spans(text))
    for match in _ORDINAL_SEASON_RE.finditer(text):
        spans.append((match.start(), match.end(), int(match.group("num"))))
    return sorted(spans)


def _title_and_season(title: str) -> tuple[str, int | None]:
    """Remove season markers from a bracket title and return the season number."""
    matches = _season_matches(title)
    masked = list(title)
    for start, end, _ in matches:
        for index in range(start, min(end, len(masked))):
            masked[index] = "\x00"
    clean_title = normalize_whitespace("".join(masked).replace("\x00", " ")).strip(" -_")
    return clean_title, (matches[0][2] if matches else None)


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one dialect-C release name; None when L1 cannot help."""
    name_draft = _parse_text(raw.name)
    if name_draft is None:
        return None
    folder_draft = _parse_text(raw.folder) if raw.folder and raw.folder != raw.name else None
    draft = merge_folder_draft(name_draft, folder_draft)
    draft = apply_release_progress(draft, context)
    if context is not None and context.release_progress is not None:
        draft = replace(draft, evidence={**draft.evidence, "release_progress": SOURCE_CONTEXT})
    if not draft.title:
        return None
    return draft.finalized().to_parse_result()


def _parse_text(text: str) -> L1Draft | None:
    base = normalize_name(text)
    if not base:
        return None
    inners = [
        span.text[1:-1].strip()
        for span in find_anchors_of_kind(base, AnchorKind.BRACKET)
    ]
    if not inners:
        return None  # no bracket flow: not dialect-C shaped

    # 标签判定用中文特征（语言/版本噪声标签必含中文字样）而非
    # fields.is_likely_fansub 的 search 语义——后者会把 "64bitsub"（含
    # "64bit" 子串）这类组名误拒（C01 契约：64bitsub 是合法 fansub）。
    def _plausible(inner: str) -> bool:
        return not _is_technical(inner) and _LABEL_RE.search(inner) is None

    fansub = inners[0] if _plausible(inners[0]) else None
    title = next((inner for inner in inners[1:] if _plausible(inner)), None)
    if title is None:
        # 括号内全是技术/标签（"[LinRip] 标题 [BDRip 1080p FLAC][简繁日内封]"）：
        # 标题在 bracket 之间的正文里——挖空 bracket 取正文（dmhy 实测语料）。
        masked = list(base)
        for span in find_anchors_of_kind(base, AnchorKind.BRACKET):
            for index in range(span.start, min(span.end, len(masked))):
                masked[index] = " "
        body = separators_to_spaces(normalize_whitespace("".join(masked)))
        title = body.strip(" -") if body else None
    episode = next((int(inner) for inner in inners if inner.isdigit()), None)

    anitopy = parse_with_anitopy(text)
    if title is None:
        title = normalize_whitespace(anitopy.get("anime_title", ""))
    if not title:
        return None

    title, season = _title_and_season(title)
    if not title:
        return None

    segment = Segment.EPISODE if episode is not None else (
        Segment.SEASON_PACK if season is not None else Segment.EPISODE
    )
    level = base_level(title=title, season=season, episode=episode, segment=segment)
    if _anitopy_conflict(anitopy, episode=episode):
        level = downgrade(level)

    return L1Draft(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=level,
        missing_fields=missing_fields_for(
            title=title, season=None, episode=episode, segment=Segment.EPISODE
        ),
        evidence={
            "title": SOURCE_NAME,
            "season": SOURCE_NAME if season is not None else SOURCE_NONE,
            "episode": SOURCE_NAME if episode is not None else SOURCE_NONE,
            "segment": SOURCE_NAME,
            "fansub": SOURCE_NAME if fansub is not None else SOURCE_NONE,
        },
    )


def _is_technical(inner: str) -> bool:
    """方括号内容是纯技术标签（"1080p" / "HEVC-10bit" / "BDRip 1080p FLAC"）。

    整串 fullmatch 之外按分隔符分词，全部 token 都是技术词/数字即判定——
    否则 "[1080p]" 这类复合标签会被当成标题（dmhy 真实语料 F 轮实测缺陷）。
    """
    if _TECH_INNER_RE.fullmatch(inner) is not None:
        return True
    tokens = re.split(r"[\s\-_&×/.]+", inner)
    if not tokens or any(not token for token in tokens):
        return False
    return all(
        _TECH_INNER_RE.fullmatch(token) is not None
        or token.isdigit()
        or (len(token) <= 2 and token.isascii())  # "H"/"DL" 分隔符拆出的字母片段
        for token in tokens
    )


def _anitopy_conflict(anitopy: dict[str, str], *, episode: int | None) -> bool:
    raw = anitopy.get("episode_number")
    if raw is None or episode is None or not raw.isdigit():
        return False
    return int(raw) != episode
