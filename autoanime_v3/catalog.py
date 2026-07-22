from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .normalize import alias_key, display_title, strip_season_markers


class TitleCatalog:
    def __init__(self, aliases: Dict[str, str], season_layouts: Dict[str, List[int]], episode_defaults=None, season_defaults=None) -> None:
        self.aliases = aliases
        self.season_layouts = season_layouts
        self.episode_defaults = episode_defaults or {}
        self.season_defaults = season_defaults or {}
        serialized = json.dumps(
            {
                "aliases": self.aliases,
                "season_layouts": self.season_layouts,
                "episode_defaults": self.episode_defaults,
                "season_defaults": self.season_defaults,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.version = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def load(cls, path: Path, user_path: Optional[Path] = None) -> "TitleCatalog":
        aliases: Dict[str, str] = {}
        layouts: Dict[str, List[int]] = {}
        defaults: Dict[str, Tuple[int, int]] = {}
        season_defaults: Dict[str, int] = {}
        for current in [path, user_path]:
            if not current or not current.is_file():
                continue
            with current.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
            raw_aliases = document.get("aliases", document) if isinstance(document, dict) else {}
            if isinstance(raw_aliases, dict):
                for raw_alias, raw_title in raw_aliases.items():
                    if raw_alias == "season_layouts":
                        continue
                    key = alias_key(str(raw_alias))
                    title = display_title(str(raw_title))
                    if key and title:
                        aliases[key] = title
                        aliases.setdefault(alias_key(title), title)
            raw_layouts = document.get("season_layouts", {}) if isinstance(document, dict) else {}
            if isinstance(raw_layouts, dict):
                for title, counts in raw_layouts.items():
                    if isinstance(counts, list) and all(isinstance(value, int) and value > 0 for value in counts):
                        layouts[display_title(title)] = list(counts)
            raw_defaults = document.get("episode_defaults", {}) if isinstance(document, dict) else {}
            if isinstance(raw_defaults, dict):
                for raw_alias, value in raw_defaults.items():
                    if isinstance(value, list) and len(value) == 2:
                        try:
                            season, episode = int(value[0]), int(value[1])
                        except (TypeError, ValueError):
                            continue
                        if season >= 0 and episode > 0:
                            defaults[alias_key(raw_alias)] = (season, episode)
            raw_season_defaults = document.get("season_defaults", {}) if isinstance(document, dict) else {}
            if isinstance(raw_season_defaults, dict):
                for raw_alias, value in raw_season_defaults.items():
                    try:
                        season = int(value)
                    except (TypeError, ValueError):
                        continue
                    if season > 0:
                        season_defaults[alias_key(raw_alias)] = season
        return cls(aliases, layouts, defaults, season_defaults)

    def resolve(self, candidates: Iterable[str]) -> Optional[Tuple[str, str]]:
        for candidate in candidates:
            variants = [candidate]
            stripped = re.sub(
                r"(?:\s+(?:S(?:eason)?\s*)?\d+|\s+\d+(?:st|nd|rd|th)\s+Season|\s+II)$",
                "",
                candidate,
                flags=re.I,
            ).strip()
            if stripped and stripped != candidate:
                variants.append(stripped)
            season_stripped = strip_season_markers(candidate)
            if season_stripped and season_stripped not in variants:
                variants.append(season_stripped)
            parenthetical = re.sub(r"\s*[（(][^）)]*$", "", candidate).strip()
            if parenthetical and parenthetical not in variants:
                variants.append(parenthetical)
            for variant in variants:
                key = alias_key(variant)
                if key in self.aliases:
                    return self.aliases[key], candidate
        return None

    def default_episode(self, candidates: Iterable[str]) -> Optional[Tuple[int, int]]:
        for candidate in candidates:
            key = alias_key(candidate)
            if key in self.episode_defaults:
                return self.episode_defaults[key]
        return None

    def default_season(self, candidates: Iterable[str]) -> Optional[int]:
        for candidate in candidates:
            key = alias_key(candidate)
            if key in self.season_defaults:
                return self.season_defaults[key]
        return None

    def remap_absolute_episode(self, title: str, season: int, episode: int, explicit_season: bool) -> Tuple[int, int, bool]:
        counts = self.season_layouts.get(title)
        if not counts:
            return season, episode, False
        if explicit_season and season > 1 and season <= len(counts):
            current_count = counts[season - 1]
            previous_count = sum(counts[: season - 1])
            if episode > current_count and episode > previous_count:
                local_episode = episode - previous_count
                if 0 < local_episode <= current_count:
                    return season, local_episode, True
            return season, episode, False
        if explicit_season or season != 1 or episode <= counts[0]:
            return season, episode, False
        remaining = episode
        for index, count in enumerate(counts, start=1):
            if remaining <= count:
                return index, remaining, True
            remaining -= count
        return season, episode, False
