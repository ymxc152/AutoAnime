from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from .models import MediaFile


VIDEO_EXTENSIONS: Set[str] = {
    ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".webm"
}
SUBTITLE_EXTENSIONS: Set[str] = {".ass", ".ssa", ".srt", ".vtt", ".sub"}
INCOMPLETE_SUFFIXES = (".!qb", ".part", ".partial", ".aria2", ".crdownload", ".tmp")
SKIP_DIR_NAMES = {"logs", ".cache", ".autoanime-v3", "@eadir", "$recycle.bin"}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _context_name(path: Path, input_root: Path, input_was_file: bool) -> str:
    if input_was_file:
        return path.parent.name
    try:
        relative = path.relative_to(input_root)
    except ValueError:
        return path.parent.name
    if len(relative.parts) > 1:
        return relative.parts[0]
    return input_root.name


def scan_media(
    source: Path,
    output_root: Optional[Path] = None,
    scope_paths: Optional[Sequence[Path]] = None,
) -> List[MediaFile]:
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(str(source))
    input_was_file = source.is_file()
    input_root = source.parent if input_was_file else source
    resolved_output = output_root.resolve() if output_root else None
    skip_output_tree = (
        resolved_output
        if resolved_output is not None and _is_relative_to(resolved_output, input_root)
        else None
    )
    candidates: Iterable[Path]
    if scope_paths:
        scoped = []
        seen = set()
        for scope in scope_paths:
            value = Path(scope).resolve(strict=False)
            values = [value] if value.is_file() else value.rglob("*") if value.is_dir() else []
            for candidate in values:
                key = str(candidate).casefold()
                if key not in seen:
                    seen.add(key)
                    scoped.append(candidate)
        candidates = scoped
    elif input_was_file:
        candidates = [source]
    else:
        candidates = source.rglob("*")

    result: List[MediaFile] = []
    for path in candidates:
        if not path.is_file():
            continue
        if skip_output_tree and _is_relative_to(path, skip_output_tree):
            continue
        lowered_parts = {part.casefold() for part in path.parts}
        if lowered_parts.intersection(name.casefold() for name in SKIP_DIR_NAMES):
            continue
        lower_name = path.name.casefold()
        if lower_name.endswith(INCOMPLETE_SUFFIXES):
            continue
        if path.suffix.casefold() not in VIDEO_EXTENSIONS:
            continue
        stat = path.stat()
        result.append(
            MediaFile(
                path=path,
                input_root=input_root,
                context_name=_context_name(path, input_root, input_was_file),
                relative_path=str(path.relative_to(input_root)),
                size=int(stat.st_size),
                mtime_ns=int(stat.st_mtime_ns),
            )
        )
    result.sort(key=lambda item: (item.relative_path.casefold(), item.mtime_ns))
    return result


def companion_subtitles(video: Path) -> Sequence[Path]:
    siblings = []
    video_stem = video.stem.casefold()
    for path in video.parent.iterdir():
        if not path.is_file() or path.suffix.casefold() not in SUBTITLE_EXTENSIONS:
            continue
        stem = path.stem.casefold()
        if stem == video_stem or stem.startswith(video_stem + ".") or video_stem.startswith(stem + "."):
            siblings.append(path)
    return sorted(siblings, key=lambda value: value.name.casefold())
