import json
import unittest
from pathlib import Path
from unittest import mock

from autoanime_v3.config import AppConfig


def _config(**overrides):
    kwargs = {
        "review_enabled": True,
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

    return MediaFile(Path("x.mkv"), Path("."), "下载", "x.mkv", 1, 1)


def _parsed(*candidates):
    from autoanime_v3.models import ParsedName

    return ParsedName(
        raw_title=candidates[0] if candidates else "X",
        season=1,
        episode=2,
        title_candidates=tuple(candidates) if candidates else ("X",),
    )


def _fake_response(content):
    """包装一个会被 json 序列化的 content。"""
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


def _fake_raw_response(raw_text):
    """直接把 raw_text 塞进 message content（用于模拟非 JSON/损坏响应）。"""
    payload = {"choices": [{"message": {"content": raw_text}}]}
    data = json.dumps(payload).encode("utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return data

    return _Response()


class ReviewAgentEnabledTests(unittest.TestCase):
    def test_enabled_requires_review_and_openai_and_key(self):
        from autoanime_v3.review import ReviewAgent

        self.assertTrue(ReviewAgent(_config()).enabled())
        self.assertFalse(ReviewAgent(_config(review_enabled=False)).enabled())
        self.assertFalse(ReviewAgent(_config(openai_enabled=False)).enabled())
        self.assertFalse(ReviewAgent(_config(openai_api_key="")).enabled())

    def test_disabled_returns_none_without_http(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config(review_enabled=False))
        with mock.patch(
            "autoanime_v3.review.urllib.request.urlopen",
            side_effect=AssertionError("should not call"),
        ):
            self.assertIsNone(agent.review(_media(), _parsed("Frieren"), [], None))


class ReviewAgentHttpTests(unittest.TestCase):
    def test_review_returns_contract_dict(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config())
        hits = [{"provider": "bgm", "name": "葬送的芙莉莲", "confidence": 0.9, "provider_id": "7"}]
        with mock.patch(
            "autoanime_v3.review.urllib.request.urlopen",
            return_value=_fake_response(
                {
                    "title_zh": "葬送的芙莉莲",
                    "confidence": 0.92,
                    "reason": "bgm 命中",
                    "verdict": "选 bgm 条目",
                }
            ),
        ):
            result = agent.review(_media(), _parsed("Frieren"), hits, None)
        self.assertEqual(result["title"], "葬送的芙莉莲")
        self.assertEqual(result["provider"], "review")
        self.assertEqual(result["season"], 1)
        self.assertEqual(result["episode"], 2)
        self.assertAlmostEqual(result["confidence"], 0.92)
        self.assertEqual(result["reason"], "bgm 命中")

    def test_non_cjk_title_rejected(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config())
        with mock.patch(
            "autoanime_v3.review.urllib.request.urlopen",
            return_value=_fake_response(
                {"title_zh": "English Only", "confidence": 0.9, "reason": "x", "verdict": "x"}
            ),
        ):
            result = agent.review(_media(), _parsed("Frieren"), [], None)
        self.assertIsNone(result)

    def test_network_failure_returns_none(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config())
        with mock.patch(
            "autoanime_v3.review.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            self.assertIsNone(agent.review(_media(), _parsed("Frieren"), [], None))

    def test_malformed_json_returns_none(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config())
        with mock.patch(
            "autoanime_v3.review.urllib.request.urlopen",
            return_value=_fake_raw_response("{bad json"),
        ):
            self.assertIsNone(agent.review(_media(), _parsed("Frieren"), [], None))

    def test_non_dict_json_returns_none(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config())
        with mock.patch(
            "autoanime_v3.review.urllib.request.urlopen",
            return_value=_fake_response("not-an-object"),
        ):
            self.assertIsNone(agent.review(_media(), _parsed("Frieren"), [], None))

    def test_prompt_instructs_anime_over_live_action(self):
        from autoanime_v3.review import ReviewAgent

        agent = ReviewAgent(_config())
        captured = {}

        def fake_urlopen(request, timeout=30.0):
            captured["body"] = request.data.decode("utf-8")
            return _fake_response(
                {"title_zh": "葬送的芙莉莲", "confidence": 0.9, "reason": "ok", "verdict": "bgm"}
            )

        with mock.patch("autoanime_v3.review.urllib.request.urlopen", side_effect=fake_urlopen):
            agent.review(_media(), _parsed("Frieren"), [], None)
        self.assertIn("动画番剧", captured["body"])
        self.assertIn("真人版", captured["body"])


if __name__ == "__main__":
    unittest.main()
