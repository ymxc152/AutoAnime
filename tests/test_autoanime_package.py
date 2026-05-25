# -*- coding: utf-8 -*-
"""autoanime 包专项测试：单文件 CLI、ShowIndex 自愈、AI 失败回退、字幕汇总。"""

import importlib.resources
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import zhconv.zhconv as zhconv_module

from autoanime import state
from autoanime.cache import show_index
from autoanime.naming import Auxiliary_IDEASS
from autoanime.zhconv_safe import Auxiliary_InitZhconvDictionarySafely


class TestAutoanimePackage(unittest.TestCase):
    def setUp(self):
        state.init_defaults()
        state.PRINTLOGFLAG = False
        # 预加载 zhconv 词典，避免 naming 首次 import 时第三方懒加载触发 ResourceWarning
        Auxiliary_InitZhconvDictionarySafely()

    def test_cli_start_getargv_single_file_mode(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            video = base / "Episode_01_test.mkv"
            video.write_bytes(b"\x00")
            argv = [str(Path("AutoAnimeMv2.py")), str(video)]
            with patch.object(sys, "argv", argv), patch("autoanime.cli.Auxiliary_InitRuntimeContext"):
                from autoanime import cli

                out = cli.Start_GetArgv()
                self.assertTrue(state.SingleFileMode)
                self.assertEqual(state.SingleFileVideoName, "Episode_01_test.mkv")
                self.assertEqual(out, (str(base), "Episode_01_test.mkv", "1"))
                self.assertEqual(state.filepath, str(base))
                self.assertEqual(state.number, "1")

    def test_show_index_self_heal_clear(self):
        cid = "test_canonical_self_heal"
        show_index.Auxiliary_SetShowOrganizationRecord(
            cid,
            {
                "canonical_id": cid,
                "organized_episodes": ["S01E01"],
                "episode_last_dst": {"S01E01": "Z:/phantom/removed/01.mkv"},
                "title_zh": "测试",
                "title_en": "",
                "title_romaji": "",
                "v": 1,
            },
        )
        has_tag, dst = show_index.Auxiliary_ShowHasOrganizedEpisode(cid, "01", "01")
        self.assertTrue(has_tag)
        self.assertIsNotNone(dst)
        self.assertTrue(show_index.Auxiliary_ShowClearOrganizedEpisode(cid, "01", "01"))
        has2, _dst2 = show_index.Auxiliary_ShowHasOrganizedEpisode(cid, "01", "01")
        self.assertFalse(has2)

    def test_processing_identification_openai_fails_uses_fallback(self):
        from autoanime.identification import Processing_Identification

        state.USEOPENAIAPI = True
        state.OPENAI_IDENTIFY_ALL = True
        state.OPENAI_FALLBACK_ON_FAILURE = True
        info5 = ("01", "01", "1", "01", "回退剧名")
        meta = {
            "NameEN": "",
            "NameRomaji": "",
            "CanonicalID": "fb1",
            "CanonicalZh": "回退剧名",
            "Source": "local_rules+traditional_api",
        }
        with patch(
            "autoanime.identification.openai_identify.Auxiliary_OpenAIIdentifyFileInfo",
            return_value=None,
        ), patch(
            "autoanime.identification.Auxiliary_ResolveFileInfoWithFallback",
            return_value=(info5, meta),
        ):
            r = Processing_Identification("S01E01.SomeTitle.mkv")
        self.assertEqual(r, info5)
        self.assertFalse(state.LastIdentificationFromAI)

    def test_ideass_single_summary_for_unparseable_subs(self):
        state.LogData = ""
        state.PRINTLOGFLAG = False
        rel = Auxiliary_IDEASS(
            "MainVideo.mkv",
            "01",
            "01",
            ["a.ass", "b.ass", "c.srt"],
        )
        self.assertIsNone(rel)
        self.assertEqual(state.LogData.count("字幕文件无法提取剧集"), 1)
        self.assertIn("跳过 3 个", state.LogData)

    def test_zhconv_safe_uses_resource_context_manager(self):
        """优先走 importlib.resources 的 with 句柄；失败时仍须关闭 get_module_res 回退流。"""
        fake_dict = {
            "SIMPONLY": ["测"],
            "TRADONLY": ["測"],
            "zh2Hans": {},
            "zh2CN": {},
            "zh2Hant": {},
            "zh2TW": {},
            "zh2HK": {},
            "zh2SG": {},
        }
        stream_payload = json.dumps(fake_dict, ensure_ascii=False).encode("utf-8")

        class _Dummy:
            def __init__(self, payload):
                self._payload = payload
                self.closed = False

            def read(self):
                return self._payload

            def close(self):
                self.closed = True

        stream = _Dummy(stream_payload)
        with patch.object(zhconv_module, "zhcdicts", None), patch.object(
            zhconv_module, "DICTIONARY", "zhcdict.json"
        ), patch.object(zhconv_module, "_DEFAULT_DICT", "zhcdict.json"), patch.object(
            zhconv_module, "get_module_res", return_value=stream
        ), patch.object(
            importlib.resources,
            "files",
            side_effect=OSError("force legacy reader"),
            create=True,
        ):
            Auxiliary_InitZhconvDictionarySafely()
        self.assertTrue(stream.closed)
        self.assertIsNotNone(zhconv_module.zhcdicts)
        self.assertIsInstance(zhconv_module.zhcdicts.get("SIMPONLY"), frozenset)


if __name__ == "__main__":
    unittest.main()
