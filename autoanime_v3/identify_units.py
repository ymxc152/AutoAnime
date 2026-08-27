"""Folder-first identify work units, with title clustering for mixed dumps."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .catalog import TitleCatalog
from .models import MediaFile, ParsedName
from .normalize import alias_key, contains_cjk, display_title, strip_season_markers
from .parser import GENERIC_CONTEXT_KEYS, parse_name


MAX_UNIT_FILES = 40
DOMINANT_TITLE_RATIO = 0.7


@dataclass(frozen=True)
class IdentifyUnit:
    folder: Path
    files: Tuple[MediaFile, ...]
    hint_title: str = ""
    generic: bool = False


def is_generic_parent(parent: Path, source_root: Path) -> bool:
    try:
        if parent.resolve() == Path(source_root).resolve():
            return True
    except OSError:
        if parent == Path(source_root):
            return True
    return alias_key(parent.name) in {alias_key(value) for value in GENERIC_CONTEXT_KEYS}


def parse_media(media: MediaFile) -> ParsedName:
    return parse_name(media.path, media.context_name)


def title_signal(media: MediaFile, catalog: TitleCatalog, parsed: Optional[ParsedName] = None) -> Tuple[str, ParsedName, str]:
    parsed = parsed or parse_media(media)
    candidates = list(parsed.title_candidates) + [media.path.parent.name, media.context_name]
    catalog_hit = catalog.resolve(candidates)
    if catalog_hit:
        return display_title(catalog_hit[0]), parsed, "catalog"
    cjk = [value for value in parsed.title_candidates if contains_cjk(value)]
    if not cjk and contains_cjk(media.path.parent.name):
        if alias_key(media.path.parent.name) not in {alias_key(value) for value in GENERIC_CONTEXT_KEYS}:
            cjk = [media.path.parent.name]
    if cjk:
        return display_title(strip_season_markers(cjk[0])), parsed, "cjk"
    return "", parsed, ""


def _chunk(files: Sequence[MediaFile], hint_title: str, folder: Path, generic: bool) -> List[IdentifyUnit]:
    if not files:
        return []
    units = []
    for start in range(0, len(files), MAX_UNIT_FILES):
        chunk = tuple(files[start : start + MAX_UNIT_FILES])
        units.append(
            IdentifyUnit(
                folder=folder,
                files=chunk,
                hint_title=hint_title if start > 0 or hint_title else "",
                generic=generic,
            )
        )
    if len(units) > 1 and hint_title:
        units = [
            IdentifyUnit(folder=unit.folder, files=unit.files, hint_title=hint_title, generic=generic)
            for unit in units
        ]
    elif len(units) > 1:
        first_title = hint_title
        patched = [units[0]]
        for unit in units[1:]:
            patched.append(
                IdentifyUnit(
                    folder=unit.folder,
                    files=unit.files,
                    hint_title=first_title,
                    generic=generic,
                )
            )
        units = patched
    return units


def _cluster_by_title(
    files: Sequence[MediaFile],
    catalog: TitleCatalog,
    folder: Path,
    generic: bool,
) -> List[IdentifyUnit]:
    buckets: Dict[str, List[MediaFile]] = defaultdict(list)
    untitled: List[MediaFile] = []
    titles: Dict[str, str] = {}
    for media in files:
        title, _parsed, _kind = title_signal(media, catalog)
        if not title:
            untitled.append(media)
            continue
        key = alias_key(title)
        titles[key] = title
        buckets[key].append(media)

    if not buckets:
        return _chunk(list(files), "", folder, generic)

    titled_count = sum(len(group) for group in buckets.values())
    dominant_key = max(buckets, key=lambda key: (len(buckets[key]), key))
    dominant_share = len(buckets[dominant_key]) / float(titled_count)
    # A dedicated show folder with one title (or a clear majority and no rival
    # titles) is one cluster. Generic dumps never collapse the whole folder.
    if not generic and len(buckets) == 1:
        return _chunk(list(files), titles[dominant_key], folder, generic)
    if not generic and dominant_share >= DOMINANT_TITLE_RATIO and len(buckets) == 1:
        return _chunk(list(files), titles[dominant_key], folder, generic)

    units: List[IdentifyUnit] = []
    for key, group in buckets.items():
        units.extend(_chunk(group, titles[key], folder, generic))
    if untitled:
        attach_hint = titles[dominant_key] if (not generic and dominant_share >= DOMINANT_TITLE_RATIO) else ""
        units.extend(_chunk(untitled, attach_hint, folder, generic))
    return units


def group_work_units(
    media_files: Iterable[MediaFile],
    catalog: TitleCatalog,
    source_root: Path,
) -> List[IdentifyUnit]:
    grouped: Dict[str, List[MediaFile]] = defaultdict(list)
    parents: Dict[str, Path] = {}
    for media in media_files:
        parent = media.path.parent
        key = str(parent).casefold()
        parents[key] = parent
        grouped[key].append(media)

    units: List[IdentifyUnit] = []
    for key, files in grouped.items():
        folder = parents[key]
        generic = is_generic_parent(folder, source_root)
        if generic:
            units.extend(_cluster_by_title(files, catalog, folder, True))
        else:
            units.extend(_cluster_by_title(files, catalog, folder, False))
    return units
