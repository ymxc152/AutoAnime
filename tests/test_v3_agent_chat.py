import sqlite3
import tempfile
import unittest
from pathlib import Path

from autoanime_v3.domain.entities import CreateProfile
from autoanime_v3.domain.errors import InvalidStateError, ValidationError
from autoanime_v3.services.agent_chat import AgentChatService
from autoanime_v3.services.changes import ChangeService
from autoanime_v3.services.memory import ShowMemoryService
from autoanime_v3.services.profiles import ProfileService
from autoanime_v3.services.reviews import ReviewService
from autoanime_v3.services.roots import RootService
from autoanime_v3.services.scans import ScanService


class AgentChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "web.sqlite3"
        self.source = self.root / "downloads"
        self.library = self.root / "library"
        self.source.mkdir()
        self.library.mkdir()
        roots = RootService(self.database)
        source_root = roots.create_root("source", self.source)
        library_root = roots.create_root("library", self.library)
        self.profile = ProfileService(self.database).create_profile(
            CreateProfile(
                name="agent-chat",
                source_root_id=source_root.id,
                library_root_id=library_root.id,
                min_confidence=86,
            )
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _open_review(self):
        (self.source / "Unknown Show S01E01.mkv").write_bytes(b"needs-review" * 64)
        ScanService(self.database).run(self.profile.id)
        return ReviewService(self.database).list_open()[0]

    def test_review_proposal_strips_destination_and_applies(self):
        review = self._open_review()
        service = AgentChatService(
            self.database,
            chat_completion=lambda messages: """```json
            {"title_zh":"人工确认番","media_type":"episode","season":1,"episode":1,
             "destination":"D:/invented/path","path":"bad","action":"move","reason":"人工判断"}
            ```""",
        )
        session = service.open_session("review", review.id)
        self.assertEqual(session["messages"][0]["role"], "system")
        updated = service.add_message(session["id"], "请确认这部番")
        self.assertEqual(updated["proposal"]["title"], "人工确认番")
        for forbidden in ("destination", "path", "action"):
            self.assertNotIn(forbidden, updated["proposal"])

        applied = service.apply(session["id"])
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["status"], "applied")
        self.assertNotEqual(ReviewService(self.database).get(review.id).status, "open")
        with self.assertRaises(InvalidStateError):
            service.add_message(session["id"], "再改一次")

    def test_library_proposal_applies_correction_and_memory(self):
        show = ChangeService(self.database).create_show("旧名")
        service = AgentChatService(
            self.database,
            chat_completion=lambda messages: '{"title":"新中文名","aliases":["Old Name"],"reason":"用户确认"}',
        )
        session = service.open_session("library", show.id)
        service.add_message(session["id"], "改为新的中文名")
        applied = service.apply(session["id"])
        self.assertEqual(applied["result"]["canonical_title"], "新中文名")

        connection = sqlite3.connect(str(self.database))
        try:
            row = connection.execute(
                "SELECT canonical_title, title_locked FROM shows WHERE id = ?", (show.id,)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("新中文名", 1))
        memory = ShowMemoryService(self.database).list()
        self.assertTrue(
            any(item["canonical_title"] == "新中文名" and item["source"] == "library_correction" for item in memory)
        )

    def test_forbidden_only_proposal_cannot_apply(self):
        review = self._open_review()
        service = AgentChatService(
            self.database,
            chat_completion=lambda messages: '{"destination":"D:/bad","action":"move"}',
        )
        session = service.open_session("review", review.id)
        updated = service.add_message(session["id"], "直接整理")
        self.assertIsNone(updated["proposal"])
        with self.assertRaises(ValidationError):
            service.apply(session["id"])


if __name__ == "__main__":
    unittest.main()
