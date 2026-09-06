"""Dialect A: dot-separated release names (MWeb season packs and episodes).

Dialect shape: ``Title.S02E01.1080p.Source.WEB-DL.AAC2.0.H.264-Group`` with
dots as the universal separator. Recognized traits:

- MWeb whole-season packs (no episode marker -> season pack);
- Baha / friDay / LINETV source stations (LINETV is dialect-A specific and
  therefore extended beyond the shared anchor source list);
- word-internal dots (``BanG.Dream``) and all-caps titles make the title
  reconstruction uncertain, so such drafts are downgraded one level;
- anitopy season/episode values are cross-checked against anchor extraction;
  a disagreement is a field conflict and drops the draft to LOW.

The title is a parse candidate, not final metadata. anitopy is only used as a
title fallback and as a cross-check; every structural field comes from the
shared anchor/field rules.
"""

from __future__ import annotations

import re
from dataclasses import replace

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1.anchors import (
    AnchorKind,
    AnchorSpan,
    find_anchors,
    find_anchors_of_kind,
)
from autoanime.pipeline.l1.anitopy_adapter import parse_with_anitopy
from autoanime.pipeline.l1.confidence import base_level, downgrade, missing_fields_for
from autoanime.pipeline.l1.context import (
    SOURCE_CONTEXT,
    SOURCE_NAME,
    SOURCE_NONE,
    apply_release_progress,
    merge_folder_draft,
)
from autoanime.pipeline.l1.draft import L1Draft
from autoanime.pipeline.l1.fields import (
    detect_segment,
    extract_episode,
    extract_fansub,
    extract_season,
    is_likely_fansub,
)
from autoanime.pipeline.l1.normalize import normalize_name, separators_to_spaces

# Source stations specific to dialect A; the shared anchor list already covers
# Baha / friDay / B-Global.
_EXTRA_SOURCE_RE = re.compile(r"(?<![A-Za-z])(?:LINETV|LiTV)(?![A-Za-z])", re.IGNORECASE)

# A token ending in a lowercase->uppercase transition (``BanG``) or a bare
# single letter marks a word-internal dot, unlike ordinary CamelCase words
# (``AzurLane``) which merely use dots as separators.
_WORD_INTERNAL_RE = re.compile(r"[a-z][A-Z]$")


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one dialect-A release name; None when L1 cannot help."""
    name_draft = _parse_text(raw.name)
    if name_draft is None:
        return None
    folder_draft = _parse_text(raw.folder) if raw.folder and raw.folder != raw.name else None
    draft = merge_folder_draft(name_draft, folder_draft)
    draft = apply_release_progress(draft, context)
    if context is not None and context.release_progress is not None:
        draft = replace(draft, evidence={**draft.evidence, "release_progress": SOURCE_CONTEXT})
    if not draft.title or draft.segment is None:
        # No segment landmark in name or folder (e.g. pure-bracket or batch
        # names): not a meaningful dialect-A result, hand back to the other
        # dialects instead of violating the ParseResult precondition.
        return None
    return draft.finalized().to_parse_result()


def _parse_text(text: str) -> L1Draft | None:
    base = normalize_name(text)
    if not base:
        return None
    spans = _structural_spans(base)
    if not spans:
        return None  # no structural landmark at all: not dialect-A shaped

    season = extract_season(base)
    episode = extract_episode(base)
    title = _title_from(base, spans)

    anitopy = parse_with_anitopy(text)
    if not title:
        title = separators_to_spaces(anitopy.get("anime_title", ""))
    if not title:
        return None

    word_internal, all_upper = _title_ambiguity(base, spans)
    fansub = _fansub_from(base, spans)
    segment = detect_segment(base, season=season, episode=episode)

    level = base_level(title=title, season=season, episode=episode, segment=segment)
    if word_internal or all_upper:
        level = downgrade(level)
    if _anitopy_conflict(anitopy, season=season, episode=episode):
        level = Confidence.LOW

    return L1Draft(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=level,
        missing_fields=missing_fields_for(
            title=title, season=season, episode=episode, segment=segment
        ),
        evidence={
            "title": SOURCE_NAME,
            "season": SOURCE_NAME if season is not None else SOURCE_NONE,
            "episode": SOURCE_NAME if episode is not None else SOURCE_NONE,
            "segment": SOURCE_NAME if segment is not None else SOURCE_NONE,
            "fansub": SOURCE_NAME if fansub is not None else SOURCE_NONE,
        },
    )


# 尾部 release year（"…Naru.2026.S02"→title 不含年份）；仅当剥离后标题
# 仍有 ≥2 个词时才剥，避免吃掉标题本身以年份结尾的番（如「1983」）。
_TRAILING_YEAR_RE = re.compile(r"\s+(?:19|20)\d{2}$")

# "编码-组名"尾巴（".DDP2.0-UBWEB"）：组名取最后一个 "-" 之后的部分。
_GROUP_SUFFIX_RE = re.compile(r"-(?P<group>[^-]+)$")


def _structural_spans(base: str) -> list[AnchorSpan]:
    spans = [span for span in find_anchors(base) if span.kind is not AnchorKind.BRACKET]
    spans.extend(
        AnchorSpan(AnchorKind.SOURCE, match.start(), match.end(), match.group(0))
        for match in _EXTRA_SOURCE_RE.finditer(base)
    )
    return sorted(spans, key=lambda span: (span.start, span.end))


def _title_region_start(base: str, spans: list[AnchorSpan]) -> int:
    """title region 起点：前缀 BRACKET（中式「[字幕组/季名] 包名」形态）之后。

    BRACKET 内容已由 fansub/season 通道消费（extract_fansub / 季标记降档），
    title 从最后一个前缀 bracket 之后开始，不再携带 "[…]" 残段。
    """
    limit = spans[0].start if spans else len(base)
    end = 0
    for span in find_anchors_of_kind(base, AnchorKind.BRACKET):
        if span.end <= limit:
            end = max(end, span.end)
    return end


def _title_from(base: str, spans: list[AnchorSpan]) -> str:
    region = base[_title_region_start(base, spans) : spans[0].start if spans else len(base)]
    title = separators_to_spaces(region.strip(" .-_"))
    # 尾部年份护栏：剥后仍 ≥2 词才生效——标题本身以年份结尾的番（如
    # 「1983」单词名）不受影响。
    stripped = _TRAILING_YEAR_RE.sub("", title)
    if stripped != title and len(stripped.split()) >= 2:
        title = stripped
    return title


def _title_ambiguity(base: str, spans: list[AnchorSpan]) -> tuple[bool, bool]:
    region = base[: spans[0].start] if spans else base
    tokens = [token for token in region.split(".") if token]
    word_internal = any(
        _WORD_INTERNAL_RE.search(token) is not None or (len(token) == 1 and token.isalpha())
        for token in tokens
    )
    alpha = [token for token in tokens if token.isalpha()]
    all_upper = len(alpha) >= 2 and all(token.isupper() for token in alpha)
    return word_internal, all_upper


def _fansub_from(base: str, spans: list[AnchorSpan]) -> str | None:
    tail = base[max(span.end for span in spans) :].lstrip(" -") if spans else ""
    # tail 起点落在某个 BRACKET 内部（"[AAC AVC][CHT]" 的 AAC AVC 之后）：
    # tail 是 bracket 残渣，剥完字符再放行会产出 "[CHT]"→"CHT" 这类标签。
    # 此时只有 "编码-组名" 末段（不含 bracket 的后半截）与 extract_fansub
    # 两条路可走。
    tail_start = max(span.end for span in spans) if spans else 0
    tail_in_bracket = any(
        span.start < tail_start < span.end
        for span in find_anchors_of_kind(base, AnchorKind.BRACKET)
    )
    stripped = tail.strip("[]【】.-_") if tail else ""
    if not tail_in_bracket and stripped and is_likely_fansub(stripped):
        return stripped
    # "编码-组名"尾巴（".DDP2.0-UBWEB"）：末段是组名候选（不含 bracket 残渣）。
    if not tail_in_bracket and stripped:
        match = _GROUP_SUFFIX_RE.search(stripped)
        if match is not None:
            group = match.group("group").strip()
            if group and is_likely_fansub(group):
                return group
    return extract_fansub(base)


def _anitopy_conflict(
    anitopy: dict[str, str], *, season: int | None, episode: int | None
) -> bool:
    for key, value in (("anime_season", season), ("episode_number", episode)):
        raw = anitopy.get(key)
        if raw is None or value is None or not raw.isdigit():
            continue
        if int(raw) != value:
            return True
    return False
