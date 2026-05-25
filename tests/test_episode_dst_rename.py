# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
from unittest import mock

from autoanime.episode_dst_rename import (
    Auxiliary_ParseOrganizedTag,
    BuildSortingDestPath,
    EpisodeDstRenameParams,
    PlanEpisodeDstRenames,
)


class TestEpisodeDstRename(unittest.TestCase):
    def test_parse_tag(self) -> None:
        self.assertEqual(Auxiliary_ParseOrganizedTag("S05E01"), ("05", "01"))
        self.assertEqual(Auxiliary_ParseOrganizedTag("s1e2"), ("1", "2"))
        self.assertIsNone(Auxiliary_ParseOrganizedTag("xx"))

    def test_build_sorting_dest_default(self) -> None:
        p = Path("F:/库/老剧名/Season01/S01E01.老剧名.mp4")
        dst = BuildSortingDestPath(
            p,
            "01",
            "01",
            "新剧名",
            EpisodeDstRenameParams(
                naming_style="default",
                use_title_to_ep=True,
            ),
        )
        self.assertIn("新剧名", str(dst).replace("\\", "/"))
        self.assertTrue(str(dst).replace("\\", "/").endswith("S01E01.新剧名.mp4"))

    def test_build_sorting_dest_emby(self) -> None:
        p = Path("F:/库/老剧/Season 01/Show - S01E01.mkv")
        dst = BuildSortingDestPath(
            p,
            "1",
            "1",
            "新",
            EpisodeDstRenameParams(
                naming_style="emby",
                use_title_to_ep=False,
            ),
        )
        self.assertIn("新 - S01E01", str(dst).replace("\\", "/"))

    def test_plan_mismatch_roots(self) -> None:
        rec = {
            "episode_last_dst": {
                "S01E01": "F:/a/Show1/Season1/a.mp4",
                "S01E02": "F:/b/Show2/Season1/b.mp4",
            }
        }
        with mock.patch("pathlib.Path.is_file", return_value=True):
            moves, errs = PlanEpisodeDstRenames(rec, "X", EpisodeDstRenameParams())
        self.assertIn("不同剧集根", "\n".join(errs))
        self.assertTrue(errs)

    def test_plan_empty(self) -> None:
        rec = {"episode_last_dst": {}}
        moves, errs = PlanEpisodeDstRenames(rec, "Z", EpisodeDstRenameParams())
        self.assertEqual(moves, [])
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
