"""Folder-scoped organize agent: identify units, remember, never invent destinations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from autoanime_v3.cache import ResolutionCache
from autoanime_v3.catalog import TitleCatalog
from autoanime_v3.config import AppConfig
from autoanime_v3.identify_units import group_work_units
from autoanime_v3.models import Resolution
from autoanime_v3.planner import build_plan
from autoanime_v3.resolver import Resolver
from autoanime_v3.scanner import scan_media
from autoanime_v3.services.memory import ShowMemoryService


UnitCallback = Callable[[Dict[str, Any]], None]
StartedCallback = Callable[[Dict[str, Any]], None]


class OrganizeAgent:
    """Orchestrate scan → resolve_unit → memory. Destinations stay in planner.build_plan."""

    def __init__(self, catalog: TitleCatalog, config: AppConfig, cache, memory: ShowMemoryService):
        self.catalog = catalog
        self.config = config
        self.cache = cache
        self.memory = memory
        self.resolver = Resolver(catalog, config, cache)

    def run(
        self,
        media_files: Iterable,
        source,
        library,
        on_unit: Optional[UnitCallback] = None,
        on_started: Optional[StartedCallback] = None,
    ) -> Tuple[List[Resolution], list]:
        files = list(media_files)
        units = group_work_units(files, self.catalog, source)
        if on_started is not None:
            on_started({"units": len(units), "files": len(files)})
        resolutions: List[Resolution] = []
        for unit in units:
            unit_resolutions = self.resolver.resolve_unit(unit)
            resolutions.extend(unit_resolutions)
            split = any("identify_split" in item.warnings for item in unit_resolutions)
            accepted_items = [item for item in unit_resolutions if item.accepted]
            if accepted_items and not split:
                for item in accepted_items:
                    self.memory.remember_resolution(item, source="identify_batch")
            titles = [item.canonical_title for item in unit_resolutions if item.canonical_title]
            if on_unit is not None:
                on_unit(
                    {
                        "folder": str(unit.folder),
                        "files": len(unit.files),
                        "hint_title": unit.hint_title,
                        "accepted": bool(accepted_items) and not split and len(accepted_items) == len(unit_resolutions),
                        "review": split or any(not item.accepted for item in unit_resolutions),
                        "title": titles[0] if titles else unit.hint_title,
                    }
                )
        return resolutions, build_plan(resolutions, Path(library))


def analyze_with_agent(
    adapter,
    source,
    library,
    min_confidence,
    scope_paths,
    on_unit=None,
    on_started=None,
):
    """Shared identify pipeline used by CoreScanAdapter."""
    from autoanime_v3.services.rules import RuleService

    active_rules = RuleService(adapter.database_path).get_active()
    memory = ShowMemoryService(adapter.database_path)
    memory.compact()
    learned = memory.load_overlay()
    overlay = {
        "aliases": dict(active_rules.document.get("aliases") or {}),
        "season_layouts": dict(active_rules.document.get("season_layouts") or {}),
        "episode_defaults": dict(active_rules.document.get("episode_defaults") or {}),
        "season_defaults": dict(active_rules.document.get("season_defaults") or {}),
    }
    overlay["aliases"].update(learned.get("aliases") or {})
    catalog = TitleCatalog.load(adapter.alias_file, overlay=overlay)
    openai = adapter._openai_config()
    metadata = adapter._metadata_config()
    config = AppConfig(
        database_path=adapter.cache_path,
        alias_file=adapter.alias_file,
        min_confidence=min_confidence,
        output_root=library,
        openai_enabled=openai["openai_enabled"],
        openai_base_url=openai["openai_base_url"],
        openai_model=openai["openai_model"],
        openai_api_key=openai["openai_api_key"],
        openai_timeout=openai["openai_timeout"],
        metadata_bangumi_enabled=metadata["metadata_bangumi_enabled"],
        metadata_tmdb_enabled=metadata["metadata_tmdb_enabled"],
        metadata_tmdb_api_key=metadata["metadata_tmdb_api_key"],
        metadata_timeout=metadata["metadata_timeout"],
        review_enabled=openai["review_enabled"],
        parse_agent_mode=openai["parse_agent_mode"],
    )
    memory = ShowMemoryService(adapter.database_path)
    with ResolutionCache(adapter.cache_path) as cache:
        agent = OrganizeAgent(catalog, config, cache, memory)
        media_files = list(scan_media(source, library, scope_paths=scope_paths))
        resolutions, entries = agent.run(
            media_files, source, library, on_unit=on_unit, on_started=on_started
        )
    return active_rules.content_hash, resolutions, entries
