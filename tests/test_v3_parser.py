import tempfile
import unittest
from pathlib import Path

from autoanime_v3.parser import parse_name
from autoanime_v3.scanner import scan_media


class ParserTests(unittest.TestCase):
    def test_parses_ubweb_folder_and_episode(self):
        parsed = parse_name(
            Path("[斗球女弹子].Dodge.Danko.2026.S01E03.1080p.WEB-DL.mkv"),
            "[斗球女弹子].Dodge.Danko.2026.S01.Complete.1080p.WEB-DL",
        )
        self.assertEqual(parsed.raw_title, "斗球女弹子")
        self.assertEqual((parsed.season, parsed.episode), (1, 3))

    def test_parses_multilingual_bracket_title(self):
        parsed = parse_name(
            Path("【今晚月色真美】[没有辣妹会对阿宅温柔！？ ／ オタクに優しいギャルはいない!? ／ Otaku ni Yasashii Gal wa Inai!?][11][1080P].mkv")
        )
        self.assertEqual(parsed.raw_title, "没有辣妹会对阿宅温柔！？")
        self.assertEqual(parsed.episode, 11)

    def test_prefers_local_episode_over_absolute_parenthetical(self):
        parsed = parse_name(Path("[BeanSub&LoliHouse] Tensei Shitara Slime Datta Ken 4th Season - 14(86) [1080p].mkv"))
        self.assertEqual((parsed.season, parsed.episode), (4, 14))

    def test_single_chinese_file(self):
        parsed = parse_name(Path("[ANi] 骸骨騎士大人異世界冒險中 第二季 - 03 [1080P][Baha].mp4"))
        self.assertEqual(parsed.raw_title, "骸骨骑士大人异世界冒险中 第二季")
        self.assertEqual((parsed.season, parsed.episode), (2, 3))

    def test_metadata_bracket_is_not_used_as_title(self):
        parsed = parse_name(Path("[ANi] 从后面来的神威先生 [年龄限制版] - 03 [1080P].mp4"))
        self.assertEqual(parsed.raw_title, "从后面来的神威先生")
        self.assertEqual(parsed.episode, 3)

    def test_language_bracket_is_not_used_as_title(self):
        parsed = parse_name(Path("[Group] 葬送的芙莉莲 第二季 Sousou no Frieren S2 [10][简体双语][1080p].mp4"))
        self.assertNotIn("简体双语", parsed.raw_title)
        self.assertEqual((parsed.season, parsed.episode), (2, 10))

    def test_scanner_does_not_drop_source_when_output_is_an_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "downloads"
            source.mkdir()
            video = source / "Show.S01E01.mkv"
            video.write_bytes(b"video")
            self.assertEqual([item.path for item in scan_media(source, root)], [video])


if __name__ == "__main__":
    unittest.main()
