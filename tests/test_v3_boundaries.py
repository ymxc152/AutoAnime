import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoanime_v3 import PARSER_VERSION, __version__
from autoanime_v3.config import AppConfig
from autoanime_v3.models import MediaFile, ParsedName
from autoanime_v3.normalize import safe_component
from autoanime_v3.parser import parse_name
from autoanime_v3.remote import OpenAIResolverAgent


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class BoundaryRegressionTests(unittest.TestCase):
    def test_runtime_sources_remain_python_38_syntax_compatible(self):
        project_root = Path(__file__).resolve().parent.parent
        for source in (project_root / "autoanime_v3").glob("*.py"):
            with self.subTest(source=source.name):
                tree = ast.parse(
                    source.read_text(encoding="utf-8"),
                    filename=str(source),
                    feature_version=(3, 8),
                )
                pep604_unions = [
                    node for node in ast.walk(tree)
                    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                ]
                self.assertEqual(pep604_unions, [], "PEP 604 unions require Python 3.10")

    def test_parser_behavior_change_bumps_cache_version(self):
        self.assertEqual(PARSER_VERSION, "3.1.1")
        self.assertEqual(__version__, "3.1.1")

    def test_trailing_episode_is_removed_from_chinese_title(self):
        parsed = parse_name(Path("测试动画 03 [1080p].mkv"))

        self.assertEqual(parsed.raw_title, "测试动画")
        self.assertEqual((parsed.season, parsed.episode), (1, 3))

    def test_windows_reserved_name_with_extension_is_prefixed(self):
        self.assertEqual(safe_component("CON.txt"), "_CON.txt")
        self.assertEqual(safe_component("aux.release"), "_aux.release")

    def test_remote_agent_rejects_non_boolean_movie_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media_path = root / "example.mkv"
            media_path.write_bytes(b"video")
            stat = media_path.stat()
            media = MediaFile(
                media_path,
                root,
                "下载",
                media_path.name,
                stat.st_size,
                stat.st_mtime_ns,
            )
            parsed = ParsedName("Example", 1, 1)
            config = AppConfig(
                database_path=root / "library.sqlite3",
                alias_file=root / "aliases.json",
                openai_enabled=True,
                openai_api_key="x",
            )
            content = json.dumps(
                {
                    "title_zh": "测试电影",
                    "season": 1,
                    "episode": 1,
                    "is_movie": "false",
                    "confidence": 0.9,
                    "reason": "fixture",
                },
                ensure_ascii=False,
            )
            response = _FakeResponse(
                {"choices": [{"message": {"content": content}}]}
            )

            with patch("autoanime_v3.remote.urllib.request.urlopen", return_value=response):
                result = OpenAIResolverAgent(config).resolve(media, parsed)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
