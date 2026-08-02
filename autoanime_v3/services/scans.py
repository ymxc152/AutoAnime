"""Orchestrate the existing scanner/resolver/planner without file writes."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from autoanime_v3.cache import ResolutionCache
from autoanime_v3.catalog import TitleCatalog
from autoanime_v3.config import AppConfig
from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.db.repositories.library import LibraryRepository
from autoanime_v3.db.repositories.scans import ScanRepository
from autoanime_v3.db.uow import SqliteUnitOfWork
from autoanime_v3.domain.entities import ScanOutcome
from autoanime_v3.domain.errors import NotFoundError, PathOutsideRootError
from autoanime_v3.planner import build_plan
from autoanime_v3.resolver import Resolver
from autoanime_v3.scanner import scan_media
from autoanime_v3.services.roots import normalize_windows_path, path_is_within
from autoanime_v3.services.rules import RuleService


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class CoreScanAdapter:
    def __init__(self, web_database, alias_file=None):
        project_root = Path(__file__).resolve().parents[2]
        self.database_path = Path(web_database)
        self.alias_file = alias_file or project_root / "autoanime_v3" / "data" / "aliases.json"
        self.cache_path = Path(web_database).with_name(Path(web_database).stem + "-resolver.sqlite3")

    def analyze(self, source, library, min_confidence):
        return self.analyze_scoped(source, library, min_confidence, None)

    def _openai_config(self):
        """Load optional remote agent settings from the Web database."""
        from autoanime_v3.security.secrets import DpapiSecretStore, EncryptedFileSecretStore
        from autoanime_v3.services.auth import SecretService
        from autoanime_v3.services.settings import (
            OPENAI_API_KEY_SECRET,
            OPENAI_BASE_URL_KEY,
            OPENAI_ENABLED_KEY,
            OPENAI_MODEL_KEY,
            OPENAI_TIMEOUT_KEY,
            SettingsService,
        )

        settings = SettingsService(self.database_path)
        enabled = bool(settings.get(OPENAI_ENABLED_KEY, False))
        base_url = str(settings.get(OPENAI_BASE_URL_KEY, "https://api.openai.com") or "https://api.openai.com")
        model = str(settings.get(OPENAI_MODEL_KEY, "gpt-4.1-mini") or "gpt-4.1-mini")
        try:
            timeout = max(5, int(settings.get(OPENAI_TIMEOUT_KEY, 30) or 30))
        except (TypeError, ValueError):
            timeout = 30
        api_key = ""
        if enabled:
            try:
                store = DpapiSecretStore()
            except OSError:
                candidates = [
                    self.database_path.parent / "secret-store",
                    self.database_path.parent.parent / "secret-store",
                ]
                store_path = next((path for path in candidates if path.exists()), candidates[0])
                store = EncryptedFileSecretStore(store_path)
            api_key = (
                SecretService(self.database_path, store).reveal_for_integration(OPENAI_API_KEY_SECRET)
                or ""
            )
        return {
            "openai_enabled": bool(enabled and api_key),
            "openai_base_url": base_url,
            "openai_model": model,
            "openai_api_key": api_key,
            "openai_timeout": timeout,
        }

    def analyze_scoped(self, source, library, min_confidence, scope_paths):
        active_rules = RuleService(self.database_path).get_active()
        catalog = TitleCatalog.load(
            self.alias_file,
            overlay=active_rules.document,
        )
        openai = self._openai_config()
        config = AppConfig(
            database_path=self.cache_path,
            alias_file=self.alias_file,
            min_confidence=min_confidence,
            output_root=library,
            openai_enabled=openai["openai_enabled"],
            openai_base_url=openai["openai_base_url"],
            openai_model=openai["openai_model"],
            openai_api_key=openai["openai_api_key"],
            openai_timeout=openai["openai_timeout"],
        )
        with ResolutionCache(self.cache_path) as cache:
            resolver = Resolver(catalog, config, cache)
            resolutions = [
                resolver.resolve(media)
                for media in scan_media(source, library, scope_paths=scope_paths)
            ]
        return active_rules.content_hash, resolutions, build_plan(resolutions, library)


class ScanService:
    def __init__(self, database_path, adapter=None):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)
        self.adapter = adapter or CoreScanAdapter(self.database_path)

    def run(self, profile_id, scope_paths=None):
        with SqliteUnitOfWork(self.database_path) as uow:
            profile = uow.connection.execute(
                "SELECT * FROM scan_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if profile is None:
                raise NotFoundError("Scan profile does not exist", {"id": profile_id})
            source = Path(
                uow.connection.execute(
                    "SELECT path FROM storage_roots WHERE id = ?", (profile["source_root_id"],)
                ).fetchone()[0]
            )
            library = Path(
                uow.connection.execute(
                    "SELECT path FROM storage_roots WHERE id = ?", (profile["library_root_id"],)
                ).fetchone()[0]
            )
        normalized_scope = []
        for value in scope_paths or []:
            target = Path(value).expanduser().resolve(strict=False)
            if not path_is_within(target, source):
                raise PathOutsideRootError(
                    "Scan scope is outside the configured source root",
                    {"root": str(source), "target": str(target)},
                )
            normalized_scope.append(target)
        if normalized_scope and hasattr(self.adapter, "analyze_scoped"):
            rule_version, resolutions, entries = self.adapter.analyze_scoped(
                source,
                library,
                int(profile["min_confidence"]) / 100.0,
                normalized_scope,
            )
        else:
            rule_version, resolutions, entries = self.adapter.analyze(
                source, library, int(profile["min_confidence"]) / 100.0
            )
        facts = LibraryRepository(self.database_path)
        media_by_path = {}
        for resolution in resolutions:
            media_by_path[normalize_windows_path(resolution.media.path)] = facts.observe_path(
                int(profile["source_root_id"]), resolution.media.path, "source", "video"
            )

        started_at = now_iso()
        review_count = 0
        plan_item_count = 0
        with SqliteUnitOfWork(self.database_path) as uow:
            current_rule_version = RuleService(self.database_path).get_active(
                uow.connection
            ).content_hash
            scans = ScanRepository(uow.connection)
            run_id = scans.create_run(
                profile_id,
                int(profile["revision"]),
                rule_version,
                {"paths": [str(path) for path in normalized_scope]},
                started_at,
            )
            result_ids = {}
            for resolution in resolutions:
                normalized = normalize_windows_path(resolution.media.path)
                media = media_by_path[normalized]
                snapshot = {
                    "path": str(resolution.media.path),
                    "relative_path": resolution.media.relative_path,
                    "context_name": resolution.media.context_name,
                    "size": resolution.media.size,
                    "mtime_ns": resolution.media.mtime_ns,
                }
                scans.add_item(
                    run_id,
                    media.id,
                    str(resolution.media.path),
                    normalized,
                    snapshot,
                    "identified" if resolution.accepted else "review",
                )
                cursor = uow.connection.execute(
                    """
                    INSERT INTO identification_results(
                        media_file_id, decision_fingerprint, parser_version, rule_version,
                        title, season_number, episode_number, media_type, confidence, accepted
                    ) VALUES (?, ?, 'v3', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        media.id,
                        resolution.fingerprint,
                        rule_version,
                        resolution.canonical_title,
                        resolution.season,
                        str(resolution.episode) if resolution.episode is not None else None,
                        "movie" if resolution.is_movie else "episode",
                        int(round(resolution.confidence * 100)),
                        int(resolution.accepted),
                    ),
                )
                result_ids[normalized] = int(cursor.lastrowid)
                for evidence in resolution.evidence:
                    uow.connection.execute(
                        """
                        INSERT INTO identification_evidence(
                            result_id, agent, field, value_json, confidence, detail_json
                        ) VALUES (?, ?, 'identity', ?, ?, ?)
                        """,
                        (
                            cursor.lastrowid,
                            evidence.agent,
                            json.dumps(evidence.value, ensure_ascii=False),
                            int(round(evidence.confidence * 100)),
                            json.dumps({"detail": evidence.detail}, ensure_ascii=False),
                        ),
                    )
                if not resolution.accepted:
                    review_count += 1
                    dedup = hashlib.sha256(
                        ("identity:%s" % media.id).encode("utf-8")
                    ).hexdigest()
                    uow.connection.execute(
                        """
                        INSERT INTO review_items(
                            scan_run_id, media_file_id, review_type, status,
                            dedup_key, payload_json
                        ) VALUES (?, ?, 'low_confidence', 'open', ?, ?)
                        """,
                        (
                            run_id,
                            media.id,
                            dedup,
                            json.dumps(resolution.to_dict(), ensure_ascii=False),
                        ),
                    )

            status = "draft" if review_count else "ready"
            if current_rule_version != rule_version:
                status = "stale"
            cursor = uow.connection.execute(
                """
                INSERT INTO plans(
                    scan_run_id, profile_id, profile_revision, rule_version,
                    library_revision, revision, status, summary_json
                ) VALUES (?, ?, ?, ?, 0, 1, ?, '{}')
                """,
                (run_id, profile_id, int(profile["revision"]), rule_version, status),
            )
            plan_id = int(cursor.lastrowid)
            for entry in entries:
                if entry.destination is None:
                    continue
                normalized = normalize_windows_path(entry.resolution.media.path)
                media = media_by_path[normalized]
                source_location = next(
                    item for item in media.locations if item.role == "source" and item.state == "present"
                )
                try:
                    relative_destination = str(entry.destination.relative_to(library))
                except ValueError:
                    continue
                action = str(profile["mode"]) if entry.action == "organize" else entry.action
                execution_status = "conflict" if entry.action == "conflict" else "pending"
                uow.connection.execute(
                    """
                    INSERT INTO plan_items(
                        plan_id, source_location_id, destination_root_id,
                        destination_relative_path, action, reason, risk_level,
                        source_file_index, source_size, source_mtime_ns,
                        identification_snapshot_json, execution_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        source_location.id,
                        int(profile["library_root_id"]),
                        relative_destination,
                        action,
                        entry.reason,
                        "high" if entry.action == "conflict" else "normal",
                        media.file_index,
                        media.size,
                        media.mtime_ns,
                        json.dumps(entry.resolution.to_dict(), ensure_ascii=False),
                        execution_status,
                    ),
                )
                plan_item_count += 1
                if entry.action == "conflict":
                    review_count += 1
            statistics = {
                "discovered": len(resolutions),
                "reviews": review_count,
                "plan_items": plan_item_count,
            }
            scans.finish(run_id, statistics, now_iso())
            uow.connection.execute(
                "UPDATE plans SET summary_json = ? WHERE id = ?",
                (json.dumps(statistics), plan_id),
            )
            uow.commit()
        from autoanime_v3.services.plans import PlanService

        plans = PlanService(self.database_path)
        plans.auto_apply_safe(plan_id)
        final_status = plans.get(plan_id).status
        return ScanOutcome(
            run_id, plan_id, len(resolutions), review_count, plan_item_count, final_status
        )
