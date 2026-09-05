"""Dialect D recognizer: CJK subtitle-group release names.

Typical shape ( Fansub group + Chinese title + inline season word + bracket
episode + noise)::

    [云光字幕组]葬送的芙莉莲 第二季 Sousou no Frieren S2 [10][END][简体双语][1080p]招募翻译.mp4
    [Skymoon] 魔法光源股份有限公司 第二季 [06].mp4

Folder names may carry the Chinese title for scene-style derived filenames::

    folder: [骸骨骑士大人异世界冒险中 第二季].Gaikotsu...S02.Complete...
    name:   Gaikotsu.Kishi-sama...S02.Complete.1080p.CR.WEB-DL.H264.AAC-UBWEB.mkv

Rules implemented here, on top of the shared L1 primitives:

- Season markers: anchor seasons (``S2`` / ``Season 2`` / ``第2季``) plus
  Chinese-numeral ``第二季``-style words. Disagreement between markers inside
  the name is a field conflict (LOW); name wins over folder with a one-step
  downgrade.
- Episode: a bracket holding a bare 1-4 digit number (``[10]`` / ``[06]``,
  years excluded), else the shared episode anchors.
- Title: CJK free chunks (season words, recruitment noise and END markers
  removed); falls back to a CJK folder bracket, then to the leading latin
  chunk (word-internal dots reconstructed, year tokens dropped).
- Fansub: the first plausible bracket of the name (``Baha`` and other source
  tags never pass the plausibility check). ``ParseContext.fansub_pref`` is a
  preference for later stages and never rewrites the parsed group.
- Grading: a Chinese title with no folder context caps the level at MEDIUM;
  a latin reconstruction from word-internal dots caps at MEDIUM.
- ANi-style names (``[ANi] ...``) belong to the EP dialect and yield None here.
"""

from __future__ import annotations

import re

from autoanime.core.enums import Confidence
from autoanime.core.interfaces import ParseContext, ParseResult, RawName
from autoanime.pipeline.l1 import (
    SOURCE_FOLDER,
    SOURCE_NAME,
    AnchorKind,
    L1Draft,
    anchor_free_chunks,
    apply_release_progress,
    base_level,
    choose_prefer_name,
    detect_segment,
    downgrade,
    extract_episode,
    extract_fansub,
    find_anchors_of_kind,
    merge_levels,
    normalize_name,
    normalize_whitespace,
    separators_to_spaces,
)

_CJK_RE = re.compile(r"[一-鿿]")
_CN_SEASON_RE = re.compile(r"第\s*([一二三四五六七八九十]{1,3})\s*[季層]")
_CN_NUMERAL: dict[str, int] = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_END_TOKEN_RE = re.compile(r"(?:END|完結|完结|完)", re.IGNORECASE)
_RECRUIT_RE = re.compile(
    r"招募|招新|招聘|宣传|宣傳|寻求|尋求|合作|应援|應援|订阅|訂閱|分享|更新|网址|網址"
)
_EPISODE_BRACKET_RE = re.compile(r"\d{1,4}")
_GROUP_KEYWORD_RE = re.compile(r"字幕組?|汉化|漢化|简体|簡體|繁体|繁體|内嵌|內嵌|中字|官中")
_YEAR_TOKEN_RE = re.compile(r"(?:19|20)\d{2}")
_ANI_PREFIX_RE = re.compile(r"^[\[【]\s*ANi\s*[\]】]", re.IGNORECASE)


def has_cjk(text: str) -> bool:
    """Whether the text contains any CJK ideograph."""
    return _CJK_RE.search(text) is not None


def _cn_numeral_value(word: str) -> int | None:
    """Chinese numeral word (一..十, 十一..九十九) to int; None when unknown."""
    if "十" in word:
        tens, _, ones = word.partition("十")
        tens_value = _CN_NUMERAL.get(tens, 1) if tens else 1
        ones_value = _CN_NUMERAL.get(ones, 0) if ones else 0
        if (tens and tens not in _CN_NUMERAL) or (ones and ones not in _CN_NUMERAL):
            return None
        return tens_value * 10 + ones_value
    return _CN_NUMERAL.get(word)


def season_spans(text: str) -> list[tuple[int, int, int]]:
    """Every season marker as ``(start, end, value)`` in positional order.

    Covers anchor seasons (``S2`` / ``Season 2`` / ``第2季``) and the
    Chinese-numeral ``第二季`` form the anchors do not know.
    """
    spans: list[tuple[int, int, int]] = []
    for span in find_anchors_of_kind(text, AnchorKind.SEASON):
        if match := re.search(r"\d{1,2}", span.text):
            spans.append((span.start, span.end, int(match.group())))
    for match in _CN_SEASON_RE.finditer(text):
        if value := _cn_numeral_value(match.group(1)):
            spans.append((match.start(), match.end(), value))
    return sorted(spans)


def _title_chunks(text: str) -> list[str]:
    """Free chunks with recruitment noise, END markers and season words removed."""
    chunks: list[str] = []
    for chunk in anchor_free_chunks(text):
        if _RECRUIT_RE.search(chunk) or _END_TOKEN_RE.fullmatch(chunk.strip()):
            continue
        chunk = normalize_whitespace(_CN_SEASON_RE.sub(" ", chunk))
        if chunk:
            chunks.append(chunk)
    return chunks


def _extract_title(text: str) -> tuple[str, bool] | None:
    """Candidate title as ``(title, is_cjk)``, or None when nothing remains.

    Preference: CJK free chunks, then a CJK folder-style bracket (group and
    subtitle markers excluded), then the leading latin chunk with year tokens
    dropped (word-internal dots already turned into spaces).
    """
    chunks = _title_chunks(text)
    cjk_chunks = [chunk for chunk in chunks if has_cjk(chunk)]
    if cjk_chunks:
        return " ".join(cjk_chunks), True
    for span in find_anchors_of_kind(text, AnchorKind.BRACKET):
        inner = span.text.lstrip("[【").rstrip("]】")
        if has_cjk(inner) and not _GROUP_KEYWORD_RE.search(inner):
            inner = normalize_whitespace(_CN_SEASON_RE.sub(" ", inner))
            if inner:
                return inner, True
    for chunk in chunks:
        words = [word for word in chunk.split() if not _YEAR_TOKEN_RE.fullmatch(word)]
        if words:
            return " ".join(words), False
    return None


def _bracket_episode(text: str) -> int | None:
    """Episode from a bracket holding a bare number (``[10]``), years excluded."""
    for span in find_anchors_of_kind(text, AnchorKind.BRACKET):
        inner = span.text.lstrip("[【").rstrip("]】").strip()
        if _EPISODE_BRACKET_RE.fullmatch(inner):
            value = int(inner)
            if not 1900 <= value <= 2099:
                return value
    return None


def _episode_of(text: str) -> int | None:
    """A bare-number bracket wins; otherwise the shared episode anchors."""
    if (episode := _bracket_episode(text)) is not None:
        return episode
    return extract_episode(text)


def parse(raw: RawName, context: ParseContext | None = None) -> ParseResult | None:
    """Parse one CJK-dialect name; None when the shape is not this dialect."""
    plain_name = normalize_name(raw.name)
    if _ANI_PREFIX_RE.match(plain_name):
        return None
    name_text = separators_to_spaces(plain_name)
    folder_text = separators_to_spaces(normalize_name(raw.folder)) if raw.folder else None
    if not has_cjk(name_text) and not (folder_text and has_cjk(folder_text)):
        return None

    name_spans = season_spans(name_text)
    name_values = {value for _, _, value in name_spans}
    name_season = name_spans[0][2] if name_spans else None
    name_episode = _episode_of(name_text)
    name_fansub = extract_fansub(name_text)
    name_title = _extract_title(name_text)

    folder_season: int | None = None
    folder_title: tuple[str, bool] | None = None
    if folder_text:
        folder_spans = season_spans(folder_text)
        folder_season = folder_spans[0][2] if folder_spans else None
        folder_title = _extract_title(folder_text)

    season, season_src = choose_prefer_name(name_season, folder_season)
    episode, episode_src = choose_prefer_name(name_episode, None)
    fansub, fansub_src = choose_prefer_name(name_fansub, None)

    title_conflict = False
    title_from_name_latin = False
    if name_title is not None and name_title[0] and name_title[1]:
        title, title_src = name_title[0], SOURCE_NAME
        if folder_title is not None and folder_title[0] and folder_title[0] != title:
            title_conflict = True
    elif folder_title is not None and folder_title[0]:
        title, title_src = folder_title[0], SOURCE_FOLDER
    elif name_title is not None and name_title[0]:
        title, title_src = name_title[0], SOURCE_NAME
        title_from_name_latin = not name_title[1]
    else:
        return None

    segment = detect_segment(name_text, season=season, episode=episode)
    if segment is None:
        return None
    segment_src = episode_src if episode is not None else season_src

    level = base_level(title=title, season=season, episode=episode, segment=segment)
    if title_from_name_latin:
        level = merge_levels(level, Confidence.MEDIUM)
    if has_cjk(title) and raw.folder is None:
        level = merge_levels(level, Confidence.MEDIUM)
    if len(name_values) > 1:
        level = Confidence.LOW
    conflicts = 0
    if title_conflict:
        conflicts += 1
    if name_season is not None and folder_season is not None and name_season != folder_season:
        conflicts += 1
    if conflicts:
        level = downgrade(level, conflicts)

    draft = L1Draft(
        title=title,
        season=season,
        episode=episode,
        segment=segment,
        fansub=fansub,
        level=level,
        evidence={
            "title": title_src,
            "season": season_src,
            "episode": episode_src,
            "segment": segment_src,
            "fansub": fansub_src,
        },
    )
    return apply_release_progress(draft, context).finalized().to_parse_result()
