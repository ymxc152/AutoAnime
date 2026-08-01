from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from .models import ParsedName
from .normalize import alias_key, cjk_count, contains_cjk, display_title, unique_nonempty


_SXXEXX = re.compile(r"(?<![A-Za-z0-9])S(?:eason)?[ ._-]*0*(\d{1,2})[ ._-]*E(?:p(?:isode)?)?[ ._-]*0*(\d{1,4})(?!\d)", re.I)
_SEASON = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)[ ._-]*Season\b|\bSeason[ ._-]*0*(\d{1,2})\b|\bS0*(\d{1,2})(?!\d)", re.I)
_CN_SEASON = re.compile(r"第\s*([一二三四五六七八九十百\d]+)\s*季")
_CN_EPISODE = re.compile(r"第\s*0*(\d{1,4})\s*[集話话回]")
_EP_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:EP?|Episode)[ ._-]*0*(\d{1,4})(?!\d)", re.I)
_DASH_EP = re.compile(r"(?:\s|^)[-–—~～][ ._-]*0*(\d{1,4})(?:\s*\(\d{1,4}\))?(?=\D|$)", re.I)
_BRACKET_EP = re.compile(r"[\[【](\d{1,4})(?:v\d+)?[\]】]", re.I)
_STAR_EP = re.compile(r"[★☆]\s*0*(\d{1,4})\s*[★☆]")
_TRAIL_EP = re.compile(r"(?:^|\s)(\d{1,4})(?=\s*(?:[\[【(（]|$))")
_MOVIE = re.compile(r"劇場版|剧场版|電影|电影|Movie\b|Theatrical", re.I)
_QUALITY = re.compile(r"\b(?:2160|1080|720|480)[pi]?\b|\b(?:WEB[- .]?DL|WEBRip|BluRay|Baha|CR|friDay|LINETV|HEVC|AVC|H\.?26[45]|AAC|FLAC|10bit)\b", re.I)
_GROUP_ONLY = re.compile(r"^(?:ANi|MWeb|UBWEB|BeanSub|LoliHouse|NC-Raws|Lilith-Raws|喵萌奶茶屋|桜都字幕组|今晚月色真美)$", re.I)
_TECHNICAL_BRACKET = re.compile(
    r"^(?:(?:JPSC|JPTC|CHS|CHT|JPN|GB|BIG5|MP4|MKV|AVC|HEVC|AAC|FLAC|BDRip|WEBRip|ViuTV)"
    r"(?:[&+ /_-](?:JPSC|JPTC|CHS|CHT|JPN|GB|BIG5|MP4|MKV|AVC|HEVC|AAC|FLAC|BDRip|WEBRip|ViuTV))*)$"
    r"|^(?:v\d+|\d{3,4}p|[A-F0-9]{8})$",
    re.I,
)
_TITLE_METADATA = re.compile(
    r"年[龄齡]限制|無修|无修|简繁|簡繁|简体|繁体|簡體|繁體|双语|雙語|内封|內封|字幕|配音|"
    r"仅限|僅限|招募|邪龙解放版|邪龍解放版|TV版|电影$|電影$|^Movie$",
    re.I,
)
_GENERIC_CONTEXT_KEYS = {"下载", "下載", "downloads", "download", "anime", "动漫", "動漫", "video", "videos"}


_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_number(value: str) -> Optional[int]:
    if value.isdigit():
        return int(value)
    if value in _CN_NUMBERS:
        return _CN_NUMBERS[value]
    if value.startswith("十"):
        return 10 + _CN_NUMBERS.get(value[1:], 0)
    if value.endswith("十"):
        return _CN_NUMBERS.get(value[:-1], 1) * 10
    if "十" in value:
        left, right = value.split("十", 1)
        return _CN_NUMBERS.get(left, 1) * 10 + _CN_NUMBERS.get(right, 0)
    return None


def _bracket_title_candidates(stem: str) -> List[str]:
    candidates: List[str] = []
    matches = list(re.finditer(r"[\[【]([^\]】]+)[\]】]", stem))
    for index, match in enumerate(matches):
        value = match.group(1)
        if index == 0 and match.start() == 0 and len(matches) > 1:
            next_value = matches[1].group(1)
            if " / " in next_value or " ／ " in next_value or not contains_cjk(value):
                continue
        if index == 0 and match.start() == 0 and not contains_cjk(value):
            after = stem[match.end():].lstrip()
            if after and not after.startswith(("[", "【")):
                continue
        if (
            _QUALITY.search(value)
            or _GROUP_ONLY.match(value.strip())
            or _TECHNICAL_BRACKET.match(value.strip())
            or _TITLE_METADATA.search(value)
        ):
            continue
        if value.strip().isdigit():
            continue
        segments = re.split(r"\s+/\s+|\s+／\s+", value)
        selected = ""
        for segment in segments:
            if cjk_count(segment) >= 2:
                selected = segment
                break
        if not selected:
            selected = segments[0].strip()
        if len(re.findall(r"[A-Za-z]", selected)) >= 3 or cjk_count(selected) >= 2:
            candidates.append(selected)
    return candidates


def _strip_leading_groups(stem: str) -> str:
    text = stem
    match = re.match(r"^\s*(?:\[[^\]]+\]|【[^】]+】)\s*", text)
    if match:
        content = match.group(0).strip(" []【】")
        after = text[match.end():]
        is_group = bool(_GROUP_ONLY.match(content) or re.search(r"字幕|汉化|漢化|压制|壓制|发布|發佈", content))
        if re.match(r"^\d{1,2}月$", content):
            is_group = True
        if not is_group and not contains_cjk(content):
            if after.startswith(("[", "【")):
                next_match = re.match(r"^[\[【]([^\]】]+)[\]】]", after)
                next_value = next_match.group(1).strip() if next_match else ""
                is_group = bool(next_value and not next_value.isdigit() and not _TECHNICAL_BRACKET.match(next_value))
            else:
                is_group = bool(after and re.match(r"[A-Za-z0-9\u3400-\u9fff]", after))
        if is_group:
            text = after
    return text.strip()


def _title_before_episode(stem: str) -> str:
    text = _strip_leading_groups(stem)
    positions = []
    for pattern in (_SXXEXX, _CN_EPISODE, _EP_TOKEN, _DASH_EP, _BRACKET_EP, _STAR_EP, _TRAIL_EP):
        match = pattern.search(text)
        if match:
            positions.append(match.start())
    if positions:
        text = text[: min(positions)]
    for match in re.finditer(r"[\[【(（]([^\]】)）]+)[\]】)）]", text):
        metadata = match.group(1).strip()
        if (
            _QUALITY.search(metadata)
            or _TECHNICAL_BRACKET.match(metadata)
            or _TITLE_METADATA.search(metadata)
            or re.search(r"uncensored|multi[- ]?subs?|CHS|CHT|JPN|SRT|ASS", metadata, re.I)
        ):
            text = text[: match.start()]
            break
    text = re.sub(r"\b(?:19|20)\d{2}\b.*$", "", text)
    text = re.sub(r"\b(?:S\d{1,2}|Season\s*\d{1,2}|\d+(?:st|nd|rd|th)\s*Season)\b.*$", "", text, flags=re.I)
    text = re.sub(r"\bComplete\b.*$", "", text, flags=re.I)
    text = re.sub(r"\s*[-–—]?\s*(?:电影|電影|Movie)\s*$", "", text, flags=re.I)
    return display_title(text)


def _leading_cjk_title(value: str) -> str:
    text = re.sub(r"[（(](?:仅限|僅限)[^）)]*[）)]", "", value).strip()
    roman_tail = re.search(
        r"\s+[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z][A-Za-z0-9-]*){3,}(?:\s|$)", text
    )
    if roman_tail and cjk_count(text[: roman_tail.start()]) >= 3:
        return display_title(text[: roman_tail.start()])
    return ""


def _season_episode(stem: str) -> Tuple[Optional[int], Optional[int], bool, bool]:
    match = _SXXEXX.search(stem)
    if match:
        return int(match.group(1)), int(match.group(2)), True, True

    season: Optional[int] = None
    episode: Optional[int] = None
    explicit_season = False
    explicit_episode = False
    cn_season = _CN_SEASON.search(stem)
    if cn_season:
        season = _cn_number(cn_season.group(1))
        explicit_season = season is not None
    if season is None:
        season_match = _SEASON.search(stem)
        if season_match:
            season = int(next(value for value in season_match.groups() if value is not None))
            explicit_season = True
    for pattern in (_CN_EPISODE, _EP_TOKEN, _DASH_EP, _STAR_EP, _BRACKET_EP, _TRAIL_EP):
        episode_match = pattern.search(stem)
        if episode_match:
            episode = int(episode_match.group(1))
            explicit_episode = True
            break
    return season, episode, explicit_season, explicit_episode


def _release_tag(stem: str) -> str:
    known = ("Baha", "friDay", "LINETV", "CR", "Netflix", "Disney+", "AMZN", "ABEMA")
    found = [value for value in known if re.search(r"(?<![A-Za-z])" + re.escape(value) + r"(?![A-Za-z])", stem, re.I)]
    if found:
        return found[0]
    group = re.search(r"-([A-Za-z][A-Za-z0-9]{2,15})$", stem)
    return group.group(1) if group else ""


def parse_name(path: Path, context_name: str = "") -> ParsedName:
    stem = path.stem
    season, episode, explicit_season, explicit_episode = _season_episode(stem)
    folder_season, _, folder_explicit_season, _ = _season_episode(context_name)
    if season is None and folder_season is not None:
        season = folder_season
        explicit_season = folder_explicit_season
    is_movie = bool(_MOVIE.search(stem) or _MOVIE.search(context_name))
    if is_movie and episode is None:
        season, episode = 1, 1
    if episode is not None and season is None:
        season = 1

    bracket_candidates = _bracket_title_candidates(stem)
    generic_context = alias_key(context_name) in {alias_key(value) for value in _GENERIC_CONTEXT_KEYS}
    folder_brackets = [] if generic_context else _bracket_title_candidates(context_name)
    file_title = _title_before_episode(stem)
    folder_title = "" if generic_context else _title_before_episode(context_name)
    leading_file_title = _leading_cjk_title(file_title)
    leading_folder_title = _leading_cjk_title(folder_title)
    bracket_cjk = [value for value in bracket_candidates if contains_cjk(value)]
    bracket_other = [value for value in bracket_candidates if not contains_cjk(value)]
    folder_cjk = [value for value in folder_brackets if contains_cjk(value)]
    folder_other = [value for value in folder_brackets if not contains_cjk(value)]
    candidates = unique_nonempty(
        bracket_cjk
        + folder_cjk
        + [leading_file_title, leading_folder_title, file_title, folder_title]
        + bracket_other
        + folder_other
    )
    raw_title = candidates[0] if candidates else display_title(file_title or folder_title)
    warnings: List[str] = []
    if episode is None and not is_movie:
        warnings.append("episode_missing")
    if not raw_title:
        warnings.append("title_missing")
    if raw_title and not contains_cjk(raw_title):
        warnings.append("title_not_chinese")
    return ParsedName(
        raw_title=raw_title,
        season=season,
        episode=episode,
        is_movie=is_movie,
        explicit_season=explicit_season,
        explicit_episode=explicit_episode,
        title_candidates=tuple(candidates),
        release_tag=_release_tag(stem),
        warnings=tuple(warnings),
    )
