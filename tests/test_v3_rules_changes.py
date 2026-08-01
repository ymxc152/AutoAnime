import tempfile
import unittest
from pathlib import Path


class RulesAndChangesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "web.sqlite3"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_rule_revisions_validate_activate_and_rollback_immutably(self):
        from autoanime_v3.domain.errors import ValidationError
        from autoanime_v3.services.rules import RuleService

        service = RuleService(self.database)
        rule_set = service.create_set("默认规则")
        invalid = service.create_revision(rule_set.id, {"aliases": []})
        with self.assertRaises(ValidationError):
            service.validate(invalid.id)

        first = service.create_revision(rule_set.id, {"aliases": {"Frieren": "葬送的芙莉莲"}})
        validated = service.validate(first.id)
        active = service.activate(validated.id)
        second = service.create_revision(rule_set.id, {"aliases": {"Frieren": "芙莉莲"}})
        service.validate(second.id)
        newer = service.activate(second.id)
        rolled_back = service.rollback(rule_set.id, active.id)

        self.assertNotEqual(active.content_hash, newer.content_hash)
        self.assertEqual(rolled_back.id, active.id)
        self.assertEqual(service.get_set(rule_set.id).active_revision_id, active.id)

    def test_show_change_uses_base_revision_and_preserves_old_new_values(self):
        from autoanime_v3.domain.errors import RevisionConflictError
        from autoanime_v3.services.changes import ChangeService

        service = ChangeService(self.database)
        show = service.create_show("旧标题")
        request = service.preview_show_change(
            show.id, show.revision, {"canonical_title": "新标题", "title_locked": True}, "人工纠正"
        )
        applied = service.apply(request.id)
        self.assertEqual(applied.canonical_title, "新标题")
        self.assertTrue(applied.title_locked)
        with self.assertRaises(RevisionConflictError):
            service.preview_show_change(show.id, show.revision, {"canonical_title": "过期修改"}, "冲突")


if __name__ == "__main__":
    unittest.main()

