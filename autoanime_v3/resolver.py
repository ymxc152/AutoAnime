from __future__ import annotations

from typing import List, Optional

from .repository import LibraryRepository, fingerprint
from .catalog import TitleCatalog
from .config import AppConfig
from .models import Evidence, MediaFile, Resolution
from .normalize import contains_cjk, display_title, strip_season_markers
from .parser import parse_name
from .remote import OpenAIResolverAgent


class Resolver:
    def __init__(self, catalog: TitleCatalog, config: AppConfig, cache: LibraryRepository) -> None:
        self.catalog = catalog
        self.config = config
        self.cache = cache
        self.remote = OpenAIResolverAgent(config)

    def resolve(self, media: MediaFile, use_cache: bool = True) -> Resolution:
        if use_cache:
            cached = self.cache.get(media, self.catalog.version)
            if cached is not None:
                cached.evidence.append(Evidence("cache", cached.canonical_title, 1.0, "fingerprint_match"))
                return cached

        parsed = parse_name(media.path, media.context_name)
        result = Resolution(
            media=media,
            season=parsed.season,
            episode=parsed.episode,
            is_movie=parsed.is_movie,
            release_tag=parsed.release_tag,
            warnings=list(parsed.warnings),
            fingerprint=fingerprint(media, self.catalog.version),
        )
        catalog_hit = self.catalog.resolve(parsed.title_candidates)
        if catalog_hit:
            title, matched = catalog_hit
            result.canonical_title = title
            result.confidence = 0.99
            result.evidence.append(Evidence("catalog", title, 0.99, "alias=" + matched))
            if result.episode is None:
                default_episode = self.catalog.default_episode(parsed.title_candidates)
                if default_episode is not None:
                    result.season, result.episode = default_episode
                    result.evidence.append(
                        Evidence("catalog_default", "%dx%d" % default_episode, 0.99, "explicit_special_default")
                    )
                    if "episode_missing" in result.warnings:
                        result.warnings.remove("episode_missing")
        else:
            chinese = [value for value in parsed.title_candidates if contains_cjk(value)]
            if chinese:
                result.canonical_title = (
                    strip_season_markers(chinese[0]) if parsed.explicit_season else display_title(chinese[0])
                )
                result.confidence = 0.93
                result.evidence.append(Evidence("filename", result.canonical_title, 0.93, "chinese_title"))
            elif parsed.raw_title:
                result.canonical_title = display_title(parsed.raw_title)
                result.confidence = 0.55
                result.evidence.append(Evidence("filename", result.canonical_title, 0.55, "non_chinese_unverified"))

        if not parsed.explicit_season:
            default_season = self.catalog.default_season(parsed.title_candidates)
            if default_season is not None:
                result.season = default_season
                result.evidence.append(
                    Evidence("catalog_season", str(default_season), 0.99, "explicit_season_default")
                )

        needs_remote = (
            not result.canonical_title
            or not contains_cjk(result.canonical_title)
            or result.episode is None
            or result.confidence < self.config.min_confidence
        )
        if needs_remote:
            remote = self.remote.resolve(media, parsed)
            if remote and remote["confidence"] >= 0.8:
                local_episode_conflict = parsed.explicit_episode and parsed.episode != remote["episode"]
                local_season_conflict = parsed.explicit_season and parsed.season != remote["season"]
                if local_episode_conflict or local_season_conflict:
                    result.warnings.append("remote_local_episode_conflict")
                else:
                    result.canonical_title = remote["title"]
                    result.season = remote["season"]
                    result.episode = remote["episode"]
                    result.is_movie = remote["is_movie"]
                    result.confidence = min(0.97, remote["confidence"])
                    result.evidence.append(Evidence("openai", remote["title"], result.confidence, remote["reason"]))

        if result.canonical_title and result.season and result.episode:
            new_season, new_episode, remapped = self.catalog.remap_absolute_episode(
                result.canonical_title, result.season, result.episode, parsed.explicit_season
            )
            if remapped:
                result.evidence.append(
                    Evidence("season_layout", "%dx%d" % (new_season, new_episode), 0.98, "absolute_episode_remap")
                )
                result.season, result.episode = new_season, new_episode

        required = bool(
            result.canonical_title
            and result.season is not None
            and result.episode is not None
            and result.episode > 0
            and result.season >= 0
        )
        chinese_title = contains_cjk(result.canonical_title)
        trusted_catalog = any(item.agent == "catalog" for item in result.evidence)
        result.accepted = bool(
            required and (chinese_title or trusted_catalog) and result.confidence >= self.config.min_confidence
        )
        if not result.accepted:
            if not chinese_title and not trusted_catalog:
                result.warnings.append("unverified_non_chinese_title")
            if not required:
                result.warnings.append("incomplete_identity")
            if result.confidence < self.config.min_confidence:
                result.warnings.append("confidence_below_threshold")
        if result.accepted:
            self.cache.put(result)
        return result
