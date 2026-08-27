import json
import unittest
from pathlib import Path
from unittest import mock

from autoanime_v3.config import AppConfig


def _config(**overrides):
    kwargs = {
        "parse_agent_mode": "uncertain",
        "openai_enabled": True,
        "openai_api_key": "k",
        "openai_base_url": "https://api.example.com",
        "openai_model": "gpt-4.1-mini",
        "openai_timeout": 30,
    }
    kwargs.update(overrides)
    return AppConfig(Path("x.sqlite3"), Path("aliases.json"), **kwargs)


def _media():
    from autoanime_v3.models import MediaFile

    return MediaFile(Path("Frieren S01E01.mkv"), Path("."), "下载", "Frieren S01E01.mkv", 1, 1)


def _parsed(*candidates):
    from autoanime_v3.models import ParsedName

    return ParsedName(
        raw_title=candidates[0] if candidates else "Frieren",
        season=1,
        episode=1,
        title_candidates=tuple(candidates) if candidates else ("Frieren",),
    )


def _fake_response(content):
    payload = {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}
    data = json.dumps(payload).encode("utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return data

    return _Response()


class AIParseEnabledTests(unittest.TestCase):
    def test_enabled_requires_mode_not_off_and_openai_key(self):
        from autoanime_v3.aiparse import AIParseAgent

        self.assertTrue(AIParseAgent(_config()).enabled())
        self.assertTrue(AIParseAgent(_config(parse_agent_mode="all")).enabled())
        self.assertFalse(AIParseAgent(_config(parse_agent_mode="off")).enabled())
        self.assertFalse(AIParseAgent(_config(openai_enabled=False)).enabled())
        self.assertFalse(AIParseAgent(_config(openai_api_key="")).enabled())

    def test_disabled_returns_none_without_http(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config(parse_agent_mode="off"))
        with mock.patch(
            "autoanime_v3.aiparse.urllib.request.urlopen",
            side_effect=AssertionError("should not call"),
        ):
            self.assertIsNone(agent.parse(_media(), _parsed()))


class AIParseHttpTests(unittest.TestCase):
    def test_parse_returns_language_tagged_candidates(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config())
        content = {
            "candidates": [
                {"lang": "romaji", "name": "Frieren: Beyond Journey's End"},
                {"lang": "ja", "name": "葬送のフリーレン"},
                {"lang": "zh-cn", "name": "葬送的芙莉莲"},
                {"lang": "zh-tw", "name": "葬送的芙莉蓮"},
            ],
            "reason": "文件名主标题为 Frieren",
        }
        with mock.patch(
            "autoanime_v3.aiparse.urllib.request.urlopen",
            return_value=_fake_response(content),
        ):
            result = agent.parse(_media(), _parsed())
        self.assertIsNotNone(result)
        pairs = result["candidates"]
        self.assertIn(("romaji", "Frieren: Beyond Journey's End"), pairs)
        self.assertIn(("zh-cn", "葬送的芙莉莲"), pairs)
        self.assertEqual(result["reason"], "文件名主标题为 Frieren")

    def test_unknown_lang_and_empty_name_filtered(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config())
        content = {
            "candidates": [
                {"lang": "klingon", "name": "X"},
                {"lang": "ja", "name": "   "},
                {"lang": "en", "name": "Frieren"},
            ],
            "reason": "x",
        }
        with mock.patch(
            "autoanime_v3.aiparse.urllib.request.urlopen",
            return_value=_fake_response(content),
        ):
            result = agent.parse(_media(), _parsed())
        self.assertEqual(result["candidates"], [("en", "Frieren")])

    def test_duplicate_candidates_deduplicated(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config())
        content = {
            "candidates": [
                {"lang": "en", "name": "Frieren"},
                {"lang": "en", "name": "Frieren"},
            ],
            "reason": "x",
        }
        with mock.patch(
            "autoanime_v3.aiparse.urllib.request.urlopen",
            return_value=_fake_response(content),
        ):
            result = agent.parse(_media(), _parsed())
        self.assertEqual(result["candidates"], [("en", "Frieren")])

    def test_network_failure_returns_none(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config())
        with mock.patch(
            "autoanime_v3.aiparse.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            self.assertIsNone(agent.parse(_media(), _parsed()))

    def test_non_dict_json_returns_none(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config())
        with mock.patch(
            "autoanime_v3.aiparse.urllib.request.urlopen",
            return_value=_fake_response("nope"),
        ):
            self.assertIsNone(agent.parse(_media(), _parsed()))

    def test_prompt_instructs_anime_over_live_action(self):
        from autoanime_v3.aiparse import AIParseAgent

        agent = AIParseAgent(_config())
        captured = {}

        def fake_urlopen(request, timeout=30.0):
            captured["body"] = request.data.decode("utf-8")
            return _fake_response({"candidates": [{"lang": "en", "name": "Frieren"}], "reason": "x"})

        with mock.patch("autoanime_v3.aiparse.urllib.request.urlopen", side_effect=fake_urlopen):
            agent.parse(_media(), _parsed("Frieren"))
        self.assertIn("动画番剧", captured["body"])
        self.assertIn("真人", captured["body"])


if __name__ == "__main__":
    unittest.main()
