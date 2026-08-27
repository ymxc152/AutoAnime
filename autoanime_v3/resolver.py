from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .repository import LibraryRepository, fingerprint
from .aiparse import AIParseAgent
from .catalog import TitleCatalog
from .config import AppConfig
from .identify import IdentifyAgent
from .identify_units import IdentifyUnit
from .metadata import MetadataResolverAgent
from .models import Evidence, MediaFile, ParsedName, Resolution
from .normalize import alias_key, contains_cjk, display_title, strip_season_markers
from .parser import parse_name
from .remote import OpenAIResolverAgent
from .review import ReviewAgent


class Resolver:
    def __init__(self, catalog: TitleCatalog, config: AppConfig, cache: LibraryRepository) -> None:
        self.catalog = catalog
        self.config = config
        self.cache = cache
        self.metadata = MetadataResolverAgent(config)
        self.remote = OpenAIResolverAgent(config)
        self.review = ReviewAgent(config)
        self.aiparse = AIParseAgent(config)
        self.identify = IdentifyAgent(config)

    def resolve(self, media: MediaFile, use_cache: bool = True) -> Resolution:
        unit = IdentifyUnit(folder=media.path.parent, files=(media,))
        return self.resolve_unit(unit, use_cache=use_cache)[0]

    def resolve_unit(self, unit: IdentifyUnit, use_cache: bool = True) -> List[Resolution]:
        """Identify every file in a folder/cluster, sharing sibling context."""
        cached: Dict[int, Resolution] = {}
        seeds: List[Tuple[MediaFile, ParsedName, Resolution]] = []
        for media in unit.files:
            if use_cache:
                hit = self.cache.get(media, self.catalog.version)
                if hit is not None:
                    hit.evidence.append(Evidence("cache", hit.canonical_title, 1.0, "fingerprint_match"))
                    cached[id(media)] = hit
                    continue
            parsed, result = self._machine_seed(media)
            seeds.append((media, parsed, result))

        if not seeds:
            return [cached[id(media)] for media in unit.files]

        self._apply_sibling_memory(unit, seeds)

        ai_on = self.aiparse.enabled()
        mode_all = ai_on and self.config.parse_agent_mode == "all"
        needs_any = any(self._needs_remote(result) for _media, _parsed, result in seeds)
        hits: List[dict] = []
        if needs_any or mode_all:
            if ai_on:
                # Stock IdentifyAgent already does one LLM call per folder. Extra
                # per-file aiparse.parse would hit the same OpenAI endpoint N times.
                skip_aiparse = type(self.identify) is IdentifyAgent and type(self.aiparse) is AIParseAgent
                if not skip_aiparse:
                    self._enrich_aiparse(seeds, mode_all)
                self._run_identify_batch(unit, seeds, cached)
            representative_media, representative_parsed, _rep = seeds[0]
            remote = None
            if self.metadata.enabled():
                probe = self._cluster_parsed(unit, seeds)
                remote, hits = self.metadata.resolve_all(representative_media, probe)
            if self.review.enabled():
                review_unit = getattr(self.review, "review_unit", None)
                if callable(review_unit):
                    reviewed = review_unit(
                        unit.folder.name,
                        self._file_payloads(unit, seeds, cached),
                        representative_parsed,
                        hits,
                        remote,
                    )
                else:
                    reviewed = self.review.review(
                        representative_media,
                        representative_parsed,
                        hits,
                        remote,
                    )
                if reviewed:
                    for _media, _parsed, result in seeds:
                        if reviewed.get("confidence", 0.0) < 0.8:
                            result.evidence.append(
                                Evidence(
                                    "review",
                                    reviewed["title"],
                                    reviewed["confidence"],
                                    reviewed.get("reason", ""),
                                )
                            )
                    remote = reviewed
            elif (not remote or remote["confidence"] < 0.8) and len(seeds) == 1:
                remote = self.remote.resolve(seeds[0][0], seeds[0][1])
            self._apply_remote(seeds, remote, mode_all)

        finalized: Dict[int, Resolution] = dict(cached)
        for media, parsed, result in seeds:
            finalized[id(media)] = self._finalize(result, parsed, hits)
        return [finalized[id(media)] for media in unit.files]

    def _machine_seed(self, media: MediaFile) -> Tuple[ParsedName, Resolution]:
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
        return parsed, result

    def _needs_remote(self, result: Resolution) -> bool:
        return (
            not result.canonical_title
            or not contains_cjk(result.canonical_title)
            or result.episode is None
            or result.confidence < self.config.min_confidence
        )

    def _apply_sibling_memory(
        self,
        unit: IdentifyUnit,
        seeds: List[Tuple[MediaFile, ParsedName, Resolution]],
    ) -> None:
        titles = []
        for _media, _parsed, result in seeds:
            if result.canonical_title and contains_cjk(result.canonical_title) and result.confidence >= 0.9:
                titles.append(result.canonical_title)
        if unit.hint_title and contains_cjk(unit.hint_title):
            titles.append(unit.hint_title)
        unique = []
        seen = set()
        for title in titles:
            key = alias_key(title)
            if key not in seen:
                seen.add(key)
                unique.append(title)
        if len(unique) != 1:
            return
        title = unique[0]
        for _media, parsed, result in seeds:
            if contains_cjk(result.canonical_title or ""):
                continue
            result.canonical_title = title
            result.confidence = max(result.confidence, 0.9)
            result.evidence.append(
                Evidence("sibling", title, 0.9, "folder=%s" % unit.folder.name)
            )
            if result.episode is None:
                default_episode = self.catalog.default_episode(parsed.title_candidates + (title,))
                if default_episode is not None:
                    result.season, result.episode = default_episode

    def _file_payloads(
        self,
        unit: IdentifyUnit,
        seeds: Sequence[Tuple[MediaFile, ParsedName, Resolution]],
        cached: Dict[int, Resolution],
    ) -> List[Dict[str, Any]]:
        payloads = []
        for media in unit.files:
            cached_hit = cached.get(id(media))
            if cached_hit is not None:
                payloads.append(
                    {
                        "name": media.path.name,
                        "cached_title": cached_hit.canonical_title,
                        "season": cached_hit.season,
                        "episode": cached_hit.episode,
                    }
                )
                continue
            for seed_media, parsed, result in seeds:
                if seed_media is media:
                    payloads.append(
                        {
                            "name": media.path.name,
                            "season": parsed.season,
                            "episode": parsed.episode,
                            "candidates": list(parsed.title_candidates),
                            "current_title": result.canonical_title,
                            "confidence": result.confidence,
                        }
                    )
                    break
        return payloads

    def _enrich_aiparse(
        self,
        seeds: List[Tuple[MediaFile, ParsedName, Resolution]],
        mode_all: bool,
    ) -> None:
        """Language-tag candidates from filenames; IdentifyAgent still owns the batch title."""
        for index, (media, parsed, result) in enumerate(seeds):
            if not mode_all and not self._needs_remote(result):
                continue
            parsed_ai = self.aiparse.parse(media, parsed)
            if not parsed_ai:
                continue
            extra: Tuple[Tuple[str, str], ...] = tuple(
                (str(lang), str(name))
                for lang, name in (parsed_ai.get("candidates") or [])
                if str(lang).strip() and str(name).strip()
            )
            if not extra:
                continue
            names = tuple(name for _lang, name in extra)
            parsed = replace(
                parsed,
                ai_candidates=parsed.ai_candidates + extra,
                title_candidates=parsed.title_candidates + names,
            )
            chinese = [name for _lang, name in extra if contains_cjk(name)]
            label = chinese[0] if chinese else extra[0][1]
            result.evidence.append(
                Evidence("aiparse", label, 0.7, str(parsed_ai.get("reason", "")))
            )
            seeds[index] = (media, parsed, result)

    def _run_identify_batch(
        self,
        unit: IdentifyUnit,
        seeds: List[Tuple[MediaFile, ParsedName, Resolution]],
        cached: Dict[int, Resolution],
    ) -> None:
        hints = [unit.hint_title] if unit.hint_title else []
        for _media, _parsed, result in seeds:
            if result.canonical_title:
                hints.append(result.canonical_title)
        identified = self.identify.identify(unit, self._file_payloads(unit, seeds, cached), hints)
        if not identified:
            return
        if identified.get("split"):
            by_name = {}
            for show in identified.get("shows") or []:
                for name in show.get("files") or []:
                    by_name[str(name)] = show["title_zh"]
            for media, parsed, result in seeds:
                title = by_name.get(media.path.name)
                if not title:
                    continue
                result.canonical_title = title
                result.confidence = min(0.7, identified.get("confidence", 0.5))
                result.warnings.append("identify_split")
                result.evidence.append(
                    Evidence("identify_batch", title, result.confidence, identified.get("reason", "split"))
                )
            return
        title = identified.get("title") or ""
        confidence = float(identified.get("confidence") or 0.0)
        if not title or not contains_cjk(title) or confidence < 0.8:
            if title:
                for _media, _parsed, result in seeds:
                    result.evidence.append(
                        Evidence("identify_batch", title, confidence, identified.get("reason", ""))
                    )
            return
        file_overrides = identified.get("files") or {}
        sibling_count = len(unit.files)
        for index, (media, parsed, result) in enumerate(seeds):
            result.canonical_title = title
            result.confidence = min(0.97, max(result.confidence, confidence))
            result.evidence.append(
                Evidence(
                    "identify_batch",
                    title,
                    result.confidence,
                    "folder=%s siblings=%s %s" % (unit.folder.name, sibling_count, identified.get("reason", "")),
                )
            )
            override = file_overrides.get(media.path.name) or {}
            if not parsed.explicit_season and override.get("season") is not None:
                result.season = override["season"]
            if not parsed.explicit_episode and override.get("episode") is not None:
                result.episode = override["episode"]
            media_type = override.get("media_type")
            if media_type:
                result.media_type = media_type
                result.is_movie = media_type == "movie"
            aliases = identified.get("aliases") or []
            extra = tuple(("alias", str(value)) for value in aliases if str(value).strip())
            if extra:
                parsed = replace(parsed, ai_candidates=parsed.ai_candidates + extra)
                seeds[index] = (media, parsed, result)

    def _cluster_parsed(
        self,
        unit: IdentifyUnit,
        seeds: Sequence[Tuple[MediaFile, ParsedName, Resolution]],
    ) -> ParsedName:
        media, parsed, result = seeds[0]
        titles = [result.canonical_title, unit.hint_title, unit.folder.name]
        for _media, other, other_result in seeds:
            titles.extend(other.title_candidates)
            titles.append(other_result.canonical_title)
        candidates = tuple(
            unique
            for unique in dict.fromkeys(str(value) for value in titles if str(value).strip())
        )
        return replace(parsed, title_candidates=candidates or parsed.title_candidates)

    def _apply_remote(
        self,
        seeds: List[Tuple[MediaFile, ParsedName, Resolution]],
        remote: Optional[Dict[str, Any]],
        mode_all: bool,
    ) -> None:
        if remote and remote.get("confidence", 0.0) >= 0.8:
            for media, parsed, result in seeds:
                local_episode_conflict = parsed.explicit_episode and parsed.episode != remote.get("episode")
                local_season_conflict = parsed.explicit_season and parsed.season != remote.get("season")
                if local_episode_conflict or local_season_conflict:
                    result.warnings.append("remote_local_episode_conflict")
                    continue
                result.canonical_title = remote["title"]
                if not parsed.explicit_season and remote.get("season") is not None:
                    result.season = remote["season"]
                if not parsed.explicit_episode and remote.get("episode") is not None:
                    result.episode = remote["episode"]
                result.is_movie = bool(remote.get("is_movie", result.is_movie))
                result.confidence = min(0.97, float(remote["confidence"]))
                agent = remote.get("provider") or "openai"
                result.evidence.append(Evidence(agent, remote["title"], result.confidence, remote.get("reason", "")))
        elif mode_all and remote and remote.get("provider") == "review":
            for _media, _parsed, result in seeds:
                result.confidence = min(result.confidence, 0.5)
                result.warnings.append("ai_uncertain")

    def _finalize(self, result: Resolution, parsed: ParsedName, hits: List[dict]) -> Resolution:
        by_title: Dict[str, dict] = {}
        for lang, name in parsed.ai_candidates:
            by_title.setdefault(alias_key(name), {"source": "aiparse", "lang": lang, "title": name})
        for cand in parsed.title_candidates:
            by_title.setdefault(alias_key(cand), {"source": "filename", "title": cand})
        for hit in hits:
            title = hit["name"]
            by_title[alias_key(title)] = {
                "source": "metadata",
                "provider": hit["provider"],
                "provider_id": hit["provider_id"],
                "title": title,
                "confidence": hit["confidence"],
                "is_anime": hit.get("is_anime", True),
            }
        result.candidates = list(by_title.values())

        if result.canonical_title and result.season and result.episode:
            new_season, new_episode, remapped = self.catalog.remap_absolute_episode(
                result.canonical_title, result.season, result.episode, parsed.explicit_season
            )
            if remapped:
                result.evidence.append(
                    Evidence("season_layout", "%dx%d" % (new_season, new_episode), 0.98, "absolute_episode_remap")
                )
                result.season, result.episode = new_season, new_episode

        episode_ok = result.episode is not None and (
            not isinstance(result.episode, (int, float)) or result.episode > 0
        )
        required = bool(
            result.canonical_title
            and result.season is not None
            and episode_ok
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
