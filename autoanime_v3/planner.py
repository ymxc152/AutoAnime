from __future__ import annotations

from collections import defaultdict
import hashlib
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .models import PlanEntry, Resolution
from .normalize import alias_key, safe_component, same_path
from .scanner import SUBTITLE_EXTENSIONS


def _media_type(resolution: Resolution) -> str:
    return resolution.media_type or ("movie" if resolution.is_movie else "episode")


def _number_token(value: Any) -> str:
    if isinstance(value, int):
        return "%02d" % value
    if isinstance(value, float):
        return str(value).rstrip("0").rstrip(".") if "." in str(value) else str(value)
    return safe_component(str(value), 32)


def _episode_basename(resolution: Resolution) -> str:
    title = safe_component(resolution.canonical_title)
    media_type = _media_type(resolution)
    if media_type == "movie":
        return title
    if media_type == "special":
        episode = _number_token(resolution.episode)
        label = episode if episode.casefold().startswith("sp") else "SP" + episode
        return "%s - %s" % (label, title)
    return "S%02dE%s - %s" % (resolution.season, _number_token(resolution.episode), title)


def _destination(output_root: Path, resolution: Resolution, version_label: str = "") -> Path:
    title = safe_component(resolution.canonical_title)
    media_type = _media_type(resolution)
    if media_type == "movie":
        directory = output_root / title
    elif media_type == "special":
        directory = output_root / title / "Specials"
    else:
        directory = output_root / title / ("Season %02d" % resolution.season)
    basename = _episode_basename(resolution)
    if version_label:
        basename += " [%s]" % safe_component(version_label, 50)
    return directory / (basename + resolution.media.path.suffix.lower())


def _version_label(resolution: Resolution) -> str:
    name = resolution.media.path.stem
    parts = []
    if resolution.release_tag:
        parts.append(resolution.release_tag)
    group = re.match(r"^[\[【]([^\]】]+)[\]】]", name)
    if group:
        value = group.group(1).strip()
        catalog_aliases = set()
        for evidence in resolution.evidence:
            prefix, separator, alias = evidence.detail.partition("=")
            if evidence.agent == "catalog" and separator and prefix.strip().casefold() == "alias":
                catalog_aliases.add(alias_key(alias.strip()))
        value_key = alias_key(value)
        if (
            value
            and value.casefold() not in {"1080p", "720p"}
            and value_key != alias_key(resolution.canonical_title)
            and value_key not in catalog_aliases
        ):
            parts.append(value)
    flags = (
        (r"年[龄齡]限制|uncensored|無修|无修", "Uncensored"),
        (r"中(?:文|字)配音|國語|国语|mandarin", "zh-dub"),
    )
    for pattern, label in flags:
        if re.search(pattern, name, re.I):
            parts.append(label)
    version = re.search(r"\bV(\d+)\b", name, re.I)
    if version:
        parts.append("V%s" % version.group(1))
    clean = []
    seen = set()
    for value in parts:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            clean.append(value)
    return "-".join(clean)


def _stable_source_digest(resolution: Resolution) -> str:
    source_identity = "%s\0%s" % (
        os.path.abspath(os.fspath(resolution.media.path)),
        resolution.media.relative_path.strip(),
    )
    normalized = unicodedata.normalize("NFC", source_identity.replace("\\", "/")).casefold()
    normalized = re.sub(r"/+", "/", normalized)
    return hashlib.sha1(normalized.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def build_plan(resolutions: Iterable[Resolution], output_root: Path) -> List[PlanEntry]:
    values = list(resolutions)
    grouped: Dict[Tuple[Any, ...], List[Resolution]] = defaultdict(list)
    for resolution in values:
        if resolution.accepted:
            grouped[resolution.identity_key()].append(resolution)

    plan: List[PlanEntry] = []
    reserved = set()
    intrinsic_labels = {
        id(resolution): _version_label(resolution)
        for resolution in values
        if resolution.accepted
    }
    labels = {}
    for resolution in values:
        if not resolution.accepted:
            continue
        label = intrinsic_labels[id(resolution)]
        labels[id(resolution)] = label or "version-" + _stable_source_digest(resolution)
    for versions in grouped.values():
        if len(versions) <= 1:
            continue
        label_counts = defaultdict(int)
        for resolution in versions:
            label_counts[intrinsic_labels[id(resolution)].casefold()] += 1
        for resolution in versions:
            label = intrinsic_labels[id(resolution)]
            if label and label_counts[label.casefold()] > 1:
                labels[id(resolution)] = label + "-" + _stable_source_digest(resolution)
    subtitle_dirs = {}
    assigned_subtitles = set()

    def subtitles_for(video: Path):
        parent = video.parent
        if parent not in subtitle_dirs:
            subtitle_dirs[parent] = [
                path for path in parent.iterdir()
                if path.is_file() and path.suffix.casefold() in SUBTITLE_EXTENSIONS
            ]
        video_stem = video.stem.casefold()
        return sorted(
            [
                path for path in subtitle_dirs[parent]
                if path.stem.casefold() == video_stem
                or path.stem.casefold().startswith(video_stem + ".")
                or video_stem.startswith(path.stem.casefold() + ".")
            ],
            key=lambda value: value.name.casefold(),
        )
    for resolution in values:
        if not resolution.accepted:
            plan.append(PlanEntry(resolution.media.path, None, "review", resolution, "unsafe_resolution"))
            continue
        destination = _destination(output_root, resolution, labels.get(id(resolution), ""))
        key = str(destination).casefold()
        if key in reserved:
            plan.append(PlanEntry(resolution.media.path, destination, "conflict", resolution, "duplicate_destination"))
            continue
        reserved.add(key)
        if destination.exists():
            if same_path(resolution.media.path, destination):
                plan.append(PlanEntry(resolution.media.path, destination, "skip", resolution, "already_linked"))
            else:
                plan.append(PlanEntry(resolution.media.path, destination, "conflict", resolution, "destination_exists"))
                continue
        else:
            plan.append(PlanEntry(resolution.media.path, destination, "organize", resolution))
        for subtitle in subtitles_for(resolution.media.path):
            subtitle_source_key = str(subtitle.resolve()).casefold()
            source_stem = resolution.media.path.stem
            suffix = subtitle.name[len(source_stem):] if subtitle.name.casefold().startswith(source_stem.casefold()) else subtitle.suffix
            subtitle_destination = destination.with_suffix("").with_name(destination.stem + suffix)
            subtitle_destination_key = str(subtitle_destination).casefold()
            if subtitle_source_key in assigned_subtitles:
                plan.append(
                    PlanEntry(subtitle, subtitle_destination, "conflict", resolution, "subtitle_matches_multiple_videos", str(resolution.media.path))
                )
                continue
            if subtitle_destination_key in reserved:
                plan.append(
                    PlanEntry(subtitle, subtitle_destination, "conflict", resolution, "duplicate_subtitle_destination", str(resolution.media.path))
                )
                continue
            if subtitle_destination.exists():
                action = "skip" if same_path(subtitle, subtitle_destination) else "conflict"
                reason = "already_linked" if action == "skip" else "subtitle_destination_exists"
                plan.append(PlanEntry(subtitle, subtitle_destination, action, resolution, reason, str(resolution.media.path)))
                assigned_subtitles.add(subtitle_source_key)
                reserved.add(subtitle_destination_key)
                continue
            assigned_subtitles.add(subtitle_source_key)
            reserved.add(subtitle_destination_key)
            plan.append(
                PlanEntry(subtitle, subtitle_destination, "organize", resolution, "subtitle", str(resolution.media.path))
            )
    for entry in plan:
        if entry.destination is not None:
            entry.destination_root = output_root
    return plan
