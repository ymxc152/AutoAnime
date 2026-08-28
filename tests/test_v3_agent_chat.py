import json
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
        assistant = next(item for item in updated["messages"] if item["role"] == "assistant")
        self.assertNotIn("{", assistant["content"])
        self.assertNotIn("destination", assistant["content"])
        self.assertNotIn("canonical_title", assistant["content"])
        for forbidden in ("destination", "path", "action"):
            self.assertNotIn(forbidden, updated["proposal"])

        applied = service.apply(session["id"])
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["status"], "applied")
        self.assertNotEqual(ReviewService(self.database).get(review.id).status, "open")
        with self.assertRaises(InvalidStateError):
            service.add_message(session["id"], "再改一次")
        again = service.open_session("review", review.id)
        self.assertEqual(again["status"], "open")
        self.assertNotEqual(again["id"], session["id"])

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

    def _enable_openai(self):
        from autoanime_v3.services.scans import CoreScanAdapter

        original = CoreScanAdapter._openai_config

        def fake_config(_self):
            return {
                "openai_enabled": True,
                "openai_base_url": "http://127.0.0.1/v1",
                "openai_model": "test",
                "openai_api_key": "k",
                "openai_timeout": 5,
            }

        CoreScanAdapter._openai_config = fake_config  # type: ignore[method-assign]
        self.addCleanup(lambda: setattr(CoreScanAdapter, "_openai_config", original))

    def test_chat_retries_until_a_usable_reply(self):
        review = self._open_review()
        attempts = {"count": 0}

        def complete(_messages):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return None
            return '{"title":"重试成功","season":1,"episode":1}'

        self._enable_openai()
        service = AgentChatService(self.database)
        service.chat_completion = None
        service._chat_once = lambda config, messages: complete(messages)  # type: ignore[method-assign]
        session = service.open_session("review", review.id)
        updated = service.add_message(session["id"], "再试一次")
        self.assertEqual(attempts["count"], 3)
        self.assertEqual(updated["proposal"]["title"], "重试成功")

    def test_chat_gives_up_after_three_failures(self):
        review = self._open_review()
        self._enable_openai()
        service = AgentChatService(self.database)
        service.chat_completion = None
        service._chat_once = lambda config, messages: None  # type: ignore[method-assign]
        session = service.open_session("review", review.id)
        updated = service.add_message(session["id"], "连不上")
        assistant = next(item for item in updated["messages"] if item["role"] == "assistant")
        self.assertIsNone(updated["proposal"])
        self.assertIn("3", assistant["content"])

    def test_internal_reason_is_rewritten_for_users(self):
        show = ChangeService(self.database).create_show("真实link测试")
        service = AgentChatService(
            self.database,
            chat_completion=lambda messages: json.dumps(
                {
                    "title": "测试",
                    "aliases": ["测试", "真实link测试"],
                    "reason": "用户指出先前识别有误，依据现有canonical_title与library_correction别名重新确认为测试条目",
                },
                ensure_ascii=False,
            ),
        )
        session = service.open_session("library", show.id)
        updated = service.add_message(session["id"], "重新识别")
        assistant = next(item for item in updated["messages"] if item["role"] == "assistant")
        self.assertEqual(updated["proposal"]["title"], "测试")
        self.assertNotIn("reason", updated["proposal"])
        self.assertNotIn("canonical_title", assistant["content"])
        self.assertNotIn("library_correction", assistant["content"])
        self.assertIn("测试", assistant["content"])


if __name__ == "__main__":
    unittest.main()
