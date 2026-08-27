"""Write-through library enrichment: fills metadata_records after plan execution.

Runs after the execute transaction commits, keyed by canonical_title. Never
raises into the caller: each show is wrapped, and the whole pass is optional.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Tuple

from autoanime_v3.integrations.metadata import SafeMetadataAdapter
from autoanime_v3.metadata import MetadataSearch
from autoanime_v3.services.settings import (
    METADATA_BANGUMI_ENABLED_KEY,
    METADATA_TIMEOUT_KEY,
    METADATA_TMDB_API_KEY_SECRET,
    METADATA_TMDB_ENABLED_KEY,
    SettingsService,
)

METADATA_TTL_DAYS = 30


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class MetadataEnrichmentService:
    def __init__(self, database_path, search=None):
        self.database_path = Path(database_path)
        self._search_override = search

    def enrich_shows(self, pairs: Iterable[Tuple[int, str]]) -> int:
        """pairs: (show_id, canonical_title). Returns how many records were stored."""
        pairs = [(show_id, (title or "").strip()) for show_id, title in pairs if (title or "").strip()]
        if not pairs:
            return 0
        settings = SettingsService(self.database_path)
        bangumi = bool(settings.get(METADATA_BANGUMI_ENABLED_KEY, False))
        tmdb = bool(settings.get(METADATA_TMDB_ENABLED_KEY, False))
        if not (bangumi or tmdb):
            return 0
        api_key = self._read_tmdb_key(settings)
        if tmdb and not api_key:
            tmdb = False
        if not (bangumi or tmdb):
            return 0
        try:
            timeout = max(2, int(settings.get(METADATA_TIMEOUT_KEY, 12) or 12))
        except (TypeError, ValueError):
            timeout = 12
        search = self._search_override or MetadataSearch(bangumi, tmdb, api_key or "", timeout)
        adapter = SafeMetadataAdapter(lambda title: self._find(title, search))
        stored = 0
        for show_id, title in pairs:
            try:
                result = adapter.fetch(title)
                if result.available and result.provider:
                    self._store(show_id, result.provider, result.provider_id or "", result)
                    stored += 1
            except Exception:
                continue
        return stored

    def _find(self, title, search):
        hit = search.search(title, movie=False)
        if not hit:
            return None
        return {
            "provider": hit["provider"],
            "provider_id": hit.get("provider_id") or "",
            "poster_url": hit.get("poster_url") or None,
            "synopsis": hit.get("synopsis") or None,
            "broadcast_status": hit.get("broadcast_status") or None,
        }

    def _store(self, show_id, provider, provider_id, result):
        from autoanime_v3.db.uow import SqliteUnitOfWork

        fetched = _now_iso()
        expires = (datetime.now(timezone.utc) + timedelta(days=METADATA_TTL_DAYS)).isoformat()
        with SqliteUnitOfWork(self.database_path) as uow:
            uow.connection.execute(
                """
                INSERT INTO metadata_records(
                    show_id, provider, provider_id, poster_url, poster_cache_path,
                    synopsis, broadcast_status, fetched_at, expires_at, response_digest
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, '')
                ON CONFLICT(provider, provider_id) DO UPDATE SET
                    show_id = excluded.show_id,
                    poster_url = excluded.poster_url,
                    synopsis = excluded.synopsis,
                    broadcast_status = excluded.broadcast_status,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (
                    show_id,
                    provider,
                    provider_id,
                    result.poster_url,
                    result.synopsis,
                    result.broadcast_status,
                    fetched,
                    expires,
                ),
            )
            uow.commit()

    @staticmethod
    def _read_tmdb_key(settings: SettingsService) -> str:
        from autoanime_v3.security.secrets import DpapiSecretStore, EncryptedFileSecretStore
        from autoanime_v3.services.auth import SecretService

        try:
            store = DpapiSecretStore()
        except OSError:
            candidates = [
                settings.database_path.parent / "secret-store",
                settings.database_path.parent.parent / "secret-store",
            ]
            store_path = next((path for path in candidates if path.exists()), candidates[0])
            store = EncryptedFileSecretStore(store_path)
        return SecretService(settings.database_path, store).reveal_for_integration(
            METADATA_TMDB_API_KEY_SECRET
        ) or ""
