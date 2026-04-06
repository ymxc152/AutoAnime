from unittest import TestCase
from unittest.mock import patch

import AutoAnimeMv as aam


class TestIdentification(TestCase):
    def setUp(self):
        with patch.object(aam, "Auxiliary_READConfig", return_value=None), patch.object(
            aam, "Auxiliary_LoadModule", return_value=None
        ):
            aam.Start_PATH()
        aam.PRINTLOGFLAG = False
        aam.USELINK = False
        aam.MANDATORYCOVER = True
        aam.CategoryName = ""

    def test_processing_identification_extract_episode_and_name(self):
        file_name = "[LoliHouse] 葬送的芙莉莲 - 03 [WebRip 1080p HEVC-10bit AAC ASSx2].mkv"
        result = aam.Processing_Identification(file_name)
        self.assertIsNotNone(result)

        se, ep, raw_se, raw_ep, raw_name = result
        self.assertEqual(se, "01")
        self.assertEqual(ep, "03")
        self.assertEqual(raw_ep, "03")
        self.assertEqual(raw_se, "")
        self.assertIn("葬送的芙莉莲", raw_name)

    def test_ass_language_classify(self):
        self.assertEqual(aam.Auxiliary_ASSFileCA("foo.简体.ass"), ".chs")
        self.assertEqual(aam.Auxiliary_ASSFileCA("foo.繁体.ass"), ".cht")
        self.assertEqual(aam.Auxiliary_ASSFileCA("foo.jp.ass"), ".jp")
