"""Backward-compatible profile webhook facade."""

from pathlib import Path

from autoanime_v3.db.migrations import run_migrations
from autoanime_v3.jobs.queue import JobQueue
from autoanime_v3.services.automation import WebhookSourceService


class WebhookService:
    def __init__(self, database_path, queue=None):
        self.database_path = Path(database_path)
        run_migrations(self.database_path)
        self.queue = queue or JobQueue(self.database_path)

    def submit(self, profile_id, path):
        service = WebhookSourceService(self.database_path)
        created = service.create("legacy", "generic", profile_id)
        try:
            return service.submit_token(created.token, [Path(path)])
        finally:
            service.delete(created.id, created.revision)
