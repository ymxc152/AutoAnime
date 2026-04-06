import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import AutoAnimeMv as aam


class TestRefactorFeatures(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        with patch.object(aam, "Auxiliary_READConfig", return_value=None), patch.object(
            aam, "Auxiliary_LoadModule", return_value=None
        ):
            aam.Start_PATH()
        aam.filepath = str(self.tmp_path)
        aam.Path = str(self.tmp_path)
        aam.CategoryName = ""
        aam.categoryname = ""
        aam.USELINK = False
        aam.MANDATORYCOVER = True
        aam.PRINTLOGFLAG = False
        aam.NAMING_STYLE = "default"
        aam.DRY_RUN = False
        aam.Auxiliary_InitRuntimeContext()

    def tearDown(self):
        self.tmp.cleanup()

    def test_processing_main_tuple_only_iterates_videos(self):
        with patch.object(
            aam,
            "Processing_Identification",
            return_value=("01", "01", "S01", "01", "TestAnime"),
        ) as mocked_ident, patch.object(
            aam, "Auxiliary_IDEASS", return_value=["sub1.ass"]
        ) as mocked_ideass, patch.object(
            aam, "Auxiliary_Api", return_value="TestAnime"
        ), patch.object(
            aam, "Sorting_Mv"
        ) as mocked_sort:
            aam.Processing_Main((["video1.mkv"], ["sub1.ass", "sub2.ass"]))

        self.assertEqual(mocked_ident.call_count, 1)
        self.assertEqual(mocked_ideass.call_count, 1)
        self.assertEqual(mocked_sort.call_count, 1)
        self.assertEqual(mocked_sort.call_args[0][0], "video1.mkv")

    def test_processing_identification_uses_openai_full_info_when_enabled(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"anime_name":"咒术回战","season":"2","episode":"48","special":false}'
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        with patch.object(aam, "post", return_value=DummyResponse()), patch.object(
            aam, "Auxiliary_IDEEP", side_effect=Exception("should_not_call")
        ):
            result = aam.Processing_Identification("Jujutsu Kaisen [WEB] 48.mkv")

        self.assertEqual(result, ("02", "48", "2", "48", "咒术回战"))
        self.assertTrue(aam.LastIdentificationFromAI)

    def test_processing_main_identifies_with_basename_for_nested_source(self):
        with patch.object(
            aam, "Processing_Identification", return_value=("01", "01", "S01", "01", "TestAnime")
        ) as mocked_ident, patch.object(
            aam, "Auxiliary_IDEASS", return_value=None
        ), patch.object(
            aam, "Auxiliary_Api", return_value="TestAnime"
        ), patch.object(
            aam, "Sorting_Mv"
        ) as mocked_sort:
            aam.Processing_Main((["pack\\video1.mkv"], ["pack\\video1.chs.ass"]))

        self.assertEqual(mocked_ident.call_args[0][0], "video1.mkv")
        self.assertEqual(mocked_sort.call_args.kwargs.get("SourceFilePath"), "pack\\video1.mkv")

    def test_scan_dir_recursively_finds_nested_video_and_subtitle(self):
        nested = self.tmp_path / "pack"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "Jujutsu Kaisen =48=.mkv").write_text("video", encoding="utf-8")
        (nested / "Jujutsu Kaisen S01 =48= 简体.ass").write_text("sub", encoding="utf-8")

        result = aam.Auxiliary_ScanDIR(str(self.tmp_path))
        self.assertIsInstance(result, tuple)
        videos, subtitles = result
        self.assertTrue(any(x.endswith("Jujutsu Kaisen =48=.mkv") for x in videos))
        self.assertTrue(any(x.endswith("Jujutsu Kaisen S01 =48= 简体.ass") for x in subtitles))

    def test_sorting_moves_nested_source_into_parent_output(self):
        nested = self.tmp_path / "pack"
        nested.mkdir(parents=True, exist_ok=True)
        source_file = nested / "Jujutsu Kaisen =48=.mkv"
        source_file.write_text("video", encoding="utf-8")

        aam.DRY_RUN = False
        aam.NAMING_STYLE = "emby"
        aam.Auxiliary_InitRuntimeContext()
        aam.Sorting_Mv(
            "Jujutsu Kaisen =48=.mkv",
            "Jujutsu Kaisen",
            "01",
            "48",
            None,
            "咒术回战",
            SourceFilePath=str(source_file.relative_to(self.tmp_path)),
        )

        expected_target = self.tmp_path / "咒术回战" / "Season 01" / "咒术回战 - S01E48.mkv"
        self.assertTrue(expected_target.exists())
        self.assertFalse(source_file.exists())

    def test_sorting_with_output_path_moves_to_custom_target_root(self):
        nested = self.tmp_path / "pack"
        nested.mkdir(parents=True, exist_ok=True)
        source_file = nested / "Jujutsu Kaisen =49=.mkv"
        source_file.write_text("video", encoding="utf-8")
        output_root = self.tmp_path / "organized"

        aam.DRY_RUN = False
        aam.NAMING_STYLE = "emby"
        aam.OUTPUT_PATH = str(output_root)
        aam.Auxiliary_InitRuntimeContext()
        aam.Sorting_Mv(
            "Jujutsu Kaisen =49=.mkv",
            "Jujutsu Kaisen",
            "01",
            "49",
            None,
            "咒术回战",
            SourceFilePath=str(source_file.relative_to(self.tmp_path)),
        )

        expected_target = output_root / "咒术回战" / "Season 01" / "咒术回战 - S01E49.mkv"
        self.assertTrue(expected_target.exists())
        self.assertFalse(source_file.exists())

    def test_strict_mode_prevents_move_fallback_after_link_failure(self):
        source_file = self.tmp_path / "src.mkv"
        target_file = self.tmp_path / "dst.mkv"
        source_file.write_text("video", encoding="utf-8")

        aam.USELINK = True
        aam.STRICT_MODE = True
        aam.LINKFAILSUSEMOVEFLAGS = True
        aam.Auxiliary_InitRuntimeContext()
        with patch.object(aam, "link", side_effect=OSError("[WinError 1] not supported")):
            aam.Auxiliary_ExecuteFileOperation(source_file, target_file)

        self.assertTrue(source_file.exists())
        self.assertFalse(target_file.exists())

    def test_non_strict_mode_can_move_when_link_fails(self):
        source_file = self.tmp_path / "src2.mkv"
        target_file = self.tmp_path / "dst2.mkv"
        source_file.write_text("video", encoding="utf-8")

        aam.USELINK = True
        aam.STRICT_MODE = False
        aam.LINKFAILSUSEMOVEFLAGS = True
        aam.Auxiliary_InitRuntimeContext()
        with patch.object(aam, "link", side_effect=OSError("[WinError 1] not supported")):
            aam.Auxiliary_ExecuteFileOperation(source_file, target_file)

        self.assertFalse(source_file.exists())
        self.assertTrue(target_file.exists())

    def test_filename_sanitizer_handles_windows_reserved_and_symbols(self):
        cleaned = aam.Auxiliary_SanitizePathComponent('CON<>:"/\\|?* .', 24)
        self.assertNotIn("<", cleaned)
        self.assertNotIn(">", cleaned)
        self.assertNotIn(":", cleaned)
        self.assertFalse(cleaned.endswith(" "))
        self.assertFalse(cleaned.endswith("."))
        self.assertNotEqual(cleaned.upper(), "CON")

    def test_emby_naming_and_dry_run_records_operations(self):
        video = "Frieren - 03.mkv"
        sub = "Frieren - 03.简体.ass"
        (self.tmp_path / video).write_text("video", encoding="utf-8")
        (self.tmp_path / sub).write_text("sub", encoding="utf-8")

        aam.NAMING_STYLE = "emby"
        aam.DRY_RUN = True
        aam.Auxiliary_InitRuntimeContext()
        aam.Sorting_Mv(video, "Frieren", "01", "03", [sub], "葬送的芙莉莲")

        records = aam.Runtime.operation_records
        self.assertGreaterEqual(len(records), 2)
        dst_values = [x["dst"] for x in records]
        self.assertTrue(any("Season 01" in x for x in dst_values))
        self.assertTrue(any("S01E03" in x for x in dst_values))
        self.assertTrue(any(".zh-CN.ass" in x for x in dst_values))
        self.assertTrue(all(x["status"] == "dry-run" for x in records))

    def test_persistent_cache_ttl(self):
        aam.Runtime.config.cache_ttl_seconds = 1
        aam.Auxiliary_SetPersistentCache("BGM", "k1", "v1")
        self.assertEqual(aam.Auxiliary_GetPersistentCache("BGM", "k1"), "v1")
        aam.PersistentApiCache["BGM"]["k1"]["ts"] = time.time() - 3
        self.assertIsNone(aam.Auxiliary_GetPersistentCache("BGM", "k1"))

    def test_rollback_from_log_moves_file_back(self):
        src = self.tmp_path / "src.mkv"
        dst = self.tmp_path / "dst.mkv"
        src.write_text("demo", encoding="utf-8")
        src.rename(dst)

        log_file = self.tmp_path / "ops.json"
        payload = {
            "records": [
                {
                    "timestamp": "2026-04-06 00:00:00",
                    "action": "move",
                    "src": str(src),
                    "dst": str(dst),
                    "status": "success",
                    "message": "",
                    "backup": "",
                }
            ]
        }
        log_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        aam.Auxiliary_RollbackFromLog(str(log_file))

        self.assertTrue(src.exists())
        self.assertFalse(dst.exists())

    def test_auxiliary_api_uses_json_dict_without_literal_eval(self):
        aam.USEOPENAIAPI = False
        aam.USEBANGUMIAPI = False
        aam.USETMDBAPI = False
        aam.USEBGMAPI = True
        with patch.object(
            aam,
            "Auxiliary_Http",
            return_value={"list": [{"name_cn": "葬送的芙莉莲", "name": "Frieren"}]},
        ):
            result = aam.Auxiliary_Api("Frieren")
        self.assertEqual(result, "葬送的芙莉莲")
