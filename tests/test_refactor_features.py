import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import AutoAnimeMv as aam


def _reset_aam_caches(aam_module):
    """清空 aam 的所有内存缓存，避免受真实 api_cache.json 历史数据（如污染的 TitleAliasIndex）干扰。

    必须在每个测试 setUp 里调用，因为 aam.Start_PATH() 会从默认 .cache/api_cache.json 自动加载。
    """
    aam_module.PersistentApiCache = {}
    aam_module.PersistentApiCacheDirty = False
    aam_module.TitleAliasIndexDataCache = {}
    aam_module.CanonicalTitleIndexDataCache = {}
    aam_module.ShowOrganizationIndexDataCache = {}
    aam_module.OpenAIIdentifyFileMemoryCache = {}
    aam_module.OpenAIAPIDataCache = {}
    aam_module.BgmAPIDataCache = {}
    aam_module.TMDBAPIDataCache = {}
    aam_module.BangumiAPIDataCache = {}
    aam_module.EpisodeDecisionDataCache = {}
    aam_module.ManualTitleWhitelistDataCache = {}
    aam_module.ManualTitleWhitelistMTime = 0.0
    aam_module.TMDBTvSeasonLayoutMemoryCache = {}
    aam_module.TMDBTvSeriesIdMemoryCache = {}
    aam_module.LastOpenAIFileInfoMeta = {}
    aam_module.LastOpenAIIdentifyFailure = None
    aam_module.LastIdentificationFromAI = False


class TestRefactorFeatures(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        with patch.object(aam, "Auxiliary_READConfig", return_value=None), patch.object(
            aam, "Auxiliary_LoadModule", return_value=None
        ):
            aam.Start_PATH()
        _reset_aam_caches(aam)
        aam.filepath = str(self.tmp_path)
        aam.Path = str(self.tmp_path)
        aam.CategoryName = ""
        aam.categoryname = ""
        aam.USELINK = False
        aam.MANDATORYCOVER = True
        aam.PRINTLOGFLAG = False
        aam.NAMING_STYLE = "default"
        aam.DRY_RUN = False
        aam.CACHE_DIR = str(self.tmp_path / ".cache")
        aam.Auxiliary_InitRuntimeContext()

    def tearDown(self):
        self.tmp.cleanup()

    def test_zhconv_safe_init_closes_resource_stream(self):
        class DummyStream:
            def __init__(self, payload):
                self.payload = payload
                self.closed = False

            def read(self):
                return self.payload

            def close(self):
                self.closed = True

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
        stream = DummyStream(json.dumps(fake_dict, ensure_ascii=False).encode("utf-8"))

        with patch.object(aam.zhconv_module, "zhcdicts", None), patch.object(
            aam.zhconv_module, "DICTIONARY", "zhcdict.json"
        ), patch.object(
            aam.zhconv_module, "_DEFAULT_DICT", "zhcdict.json"
        ), patch.object(
            aam.zhconv_module, "get_module_res", return_value=stream
        ):
            aam.Auxiliary_InitZhconvDictionarySafely()
            self.assertTrue(stream.closed)
            self.assertIsNotNone(aam.zhconv_module.zhcdicts)
            self.assertIsInstance(aam.zhconv_module.zhcdicts.get("SIMPONLY"), frozenset)
            self.assertIsInstance(aam.zhconv_module.zhcdicts.get("TRADONLY"), frozenset)

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

    def test_processing_main_skips_incomplete_download_file(self):
        partial_file = "[Comicat&kisssub][Sousou no Frieren S2][08][1080P][GB][MP4].mp4.!qB"
        with patch.object(aam, "Processing_Identification") as mocked_ident, patch.object(
            aam, "Sorting_Mv"
        ) as mocked_sort:
            aam.Processing_Main([partial_file])

        mocked_ident.assert_not_called()
        mocked_sort.assert_not_called()

    def test_processing_mode_refreshes_runtime_context_from_cli_like_globals(self):
        video = self.tmp_path / "video1.mkv"
        video.write_text("video", encoding="utf-8")
        output_root = self.tmp_path / "organized"

        aam.filepath = str(self.tmp_path)
        aam.categoryname = "动漫"
        aam.NAMING_STYLE = "emby"
        aam.DRY_RUN = True
        aam.STRICT_MODE = False
        aam.OUTPUT_PATH = str(output_root)

        aam.Processing_Mode(str(self.tmp_path))

        self.assertEqual(aam.Runtime.source_path, self.tmp_path)
        self.assertEqual(aam.Runtime.output_path, output_root)
        self.assertEqual(aam.Runtime.category_name, "动漫")
        self.assertEqual(aam.Runtime.config.naming_style, "emby")
        self.assertTrue(aam.Runtime.config.dry_run)
        self.assertFalse(aam.Runtime.config.strict_mode)

    def test_processing_mode_qb_callback_skips_incomplete_download_file(self):
        partial_file = "[Comicat&kisssub][Sousou no Frieren S2][08][1080P][GB][MP4].mp4.!qB"
        (self.tmp_path / partial_file).write_text("partial", encoding="utf-8")

        result = aam.Processing_Mode((str(self.tmp_path), partial_file, "1"))

        self.assertEqual(result, [])

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
        with patch.object(aam, "post", return_value=DummyResponse()) as mocked_post, patch.object(
            aam, "Auxiliary_IDEEP", side_effect=Exception("should_not_call")
        ), patch.object(
            aam,
            "Auxiliary_RemappedJujutsuKaisenSeasonEpisode",
            return_value=("3", "1", "03", "01"),
        ):
            result = aam.Processing_Identification("Jujutsu Kaisen [WEB] 48.mkv")

        self.assertEqual(result, ("03", "01", "3", "1", "咒术回战"))
        self.assertTrue(aam.LastIdentificationFromAI)
        self.assertIn("简体中文", mocked_post.call_args.kwargs["json"]["messages"][0]["content"])

    def test_openai_full_info_cache_hit_repairs_title_with_standard_cache(self):
        file_name = "[BeanSub&FZSD&LoliHouse] Jujutsu Kaisen - 59.mkv"
        aam.USEOPENAIAPI = True
        aam.OPENAI_IDENTIFY_ALL = True
        aam.Auxiliary_UpsertCanonicalTitle(
            "咒术回战", "Jujutsu Kaisen", "Jujutsu Kaisen", "Bangumi", [file_name, "Jujutsu Kaisen"]
        )
        aam.OpenAIIdentifyFileMemoryCache[file_name] = {
            "SE": "01",
            "EP": "59",
            "RAWSE": "1",
            "RAWEP": "59",
            "RAWName": "Jujutsu Kaisen",
            "NameEN": "Jujutsu Kaisen",
            "NameRomaji": "Jujutsu Kaisen",
            "CanonicalID": "",
        }

        with patch.object(
            aam,
            "Auxiliary_RemappedJujutsuKaisenSeasonEpisode",
            return_value=("3", "12", "03", "12"),
        ):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo(file_name)

        self.assertEqual(result, ("03", "12", "3", "12", "咒术回战"))
        repaired = aam.OpenAIIdentifyFileMemoryCache.get(file_name)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["RAWName"], "咒术回战")
        self.assertEqual(repaired["SE"], "03")
        self.assertEqual(repaired["EP"], "12")

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

    def test_sorting_with_output_path_skips_same_hardlinked_target_on_rerun(self):
        nested = self.tmp_path / "pack"
        nested.mkdir(parents=True, exist_ok=True)
        source_file = nested / "Jujutsu Kaisen =50=.mkv"
        source_file.write_text("video", encoding="utf-8")
        output_root = self.tmp_path / "organized"

        aam.DRY_RUN = False
        aam.NAMING_STYLE = "emby"
        aam.OUTPUT_PATH = str(output_root)
        aam.USELINK = True
        aam.Auxiliary_InitRuntimeContext()

        sorting_kwargs = dict(
            FileName="Jujutsu Kaisen =50=.mkv",
            RAWName="Jujutsu Kaisen",
            SE="01",
            EP="50",
            ASSList=None,
            ApiName="咒术回战",
            SourceFilePath=str(source_file.relative_to(self.tmp_path)),
        )

        aam.Sorting_Mv(**sorting_kwargs)
        expected_target = output_root / "咒术回战" / "Season 01" / "咒术回战 - S01E50.mkv"
        self.assertTrue(expected_target.exists())
        self.assertTrue(source_file.exists())
        self.assertTrue(aam.Auxiliary_IsSamePhysicalFile(source_file, expected_target))

        aam.Sorting_Mv(**sorting_kwargs)

        self.assertEqual(aam.Runtime.operation_records[-1]["status"], "skipped")
        self.assertEqual(aam.Runtime.operation_records[-1]["message"], "same_file")
        self.assertEqual(list(output_root.rglob("*.aam.bak.*")), [])

    def test_link_mode_keeps_existing_target_instead_of_replacing_duplicate(self):
        source_old = self.tmp_path / "old_source.mkv"
        source_new = self.tmp_path / "new_source.mkv"
        source_old.write_text("old", encoding="utf-8")
        source_new.write_text("new", encoding="utf-8")
        output_root = self.tmp_path / "organized"

        aam.DRY_RUN = False
        aam.NAMING_STYLE = "emby"
        aam.OUTPUT_PATH = str(output_root)
        aam.USELINK = True
        aam.MANDATORYCOVER = True
        aam.Auxiliary_InitRuntimeContext()

        aam.Sorting_Mv("old_source.mkv", "Frieren", "02", "08", None, "葬送的芙莉莲", SourceFilePath="old_source.mkv")
        expected_target = output_root / "葬送的芙莉莲" / "Season 02" / "葬送的芙莉莲 - S02E08.mkv"
        self.assertTrue(expected_target.exists())
        self.assertTrue(aam.Auxiliary_IsSamePhysicalFile(source_old, expected_target))

        aam.Sorting_Mv("new_source.mkv", "Sousou no Frieren", "02", "08", None, "葬送的芙莉莲", SourceFilePath="new_source.mkv")

        self.assertTrue(source_new.exists())
        self.assertTrue(aam.Auxiliary_IsSamePhysicalFile(source_old, expected_target))
        self.assertFalse(aam.Auxiliary_IsSamePhysicalFile(source_new, expected_target))
        self.assertEqual(aam.Runtime.operation_records[-1]["status"], "skipped")
        self.assertEqual(aam.Runtime.operation_records[-1]["message"], "existing_link_kept")
        self.assertEqual(list(output_root.rglob("*.aam.bak.*")), [])

    def test_duplicate_skip_still_caches_resolved_file_info(self):
        original_file = self.tmp_path / "original.mkv"
        duplicate_name = "[Comicat&kisssub][Sousou no Frieren S2][08][1080P][GB][MP4].mp4"
        duplicate_file = self.tmp_path / duplicate_name
        original_file.write_text("origin", encoding="utf-8")
        duplicate_file.write_text("dup", encoding="utf-8")
        output_root = self.tmp_path / "organized"

        aam.DRY_RUN = False
        aam.NAMING_STYLE = "emby"
        aam.OUTPUT_PATH = str(output_root)
        aam.USELINK = True
        aam.MANDATORYCOVER = True
        aam.Auxiliary_InitRuntimeContext()

        aam.Sorting_Mv("original.mkv", "Frieren", "02", "08", None, "葬送的芙莉莲", SourceFilePath="original.mkv")
        expected_target = output_root / "葬送的芙莉莲" / "Season 02" / "葬送的芙莉莲 - S02E08.mkv"
        pre_hint = aam.Auxiliary_PreDetectEpisodeHint(duplicate_name)
        self.assertIsNotNone(pre_hint)
        aam.EpisodeDecisionDataCache[pre_hint["EpisodeKey"]] = {
            "source_mtime": 0.0,
            "src": str(original_file),
            "dst": str(expected_target),
            "resolved": {
                "SE": "02",
                "EP": "08",
                "RAWSE": "2",
                "RAWEP": "08",
                "RAWName": "Sousou no Frieren S2",
                "ApiName": "葬送的芙莉莲",
                "NameEN": "Sousou no Frieren",
                "NameRomaji": "Sousou no Frieren",
                "CanonicalID": "",
            },
        }

        with patch.object(
            aam, "Processing_Identification", return_value=("02", "08", "2", "08", "Sousou no Frieren S2")
        ) as mocked_ident, patch.object(
            aam, "Auxiliary_Api", return_value="葬送的芙莉莲"
        ) as mocked_api:
            aam.Processing_Main([duplicate_name])

        mocked_ident.assert_not_called()
        mocked_api.assert_not_called()
        self.assertEqual(aam.Runtime.operation_records[-1]["message"], "newer_duplicate_kept_oldest")

        with patch.object(aam, "Processing_Identification") as mocked_ident_again, patch.object(
            aam, "Auxiliary_Api"
        ) as mocked_api_again:
            aam.Processing_Main([duplicate_name])

        mocked_ident_again.assert_not_called()
        mocked_api_again.assert_not_called()

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

    def test_start_getargv_rollback_refreshes_runtime_and_skips_operation_log(self):
        rollback_log = self.tmp_path / "rollback.json"
        rollback_log.write_text('{"records":[]}', encoding="utf-8")

        with patch.object(aam, "argv", ["AutoAnimeMv.py", "rollback", "--log", str(rollback_log)]):
            result = aam.Start_GetArgv()

        self.assertEqual(result, str(rollback_log))
        self.assertEqual(aam.RUN_COMMAND, "rollback")
        self.assertEqual(aam.Runtime.rollback_log_path, rollback_log)

        aam.Runtime.operation_log_path = self.tmp_path / "logs" / "should_not_exist.json"
        aam.Auxiliary_WriteOperationLog()
        self.assertFalse(aam.Runtime.operation_log_path.exists())

    def test_auxiliary_api_uses_json_dict_without_literal_eval(self):
        aam.USEOPENAIAPI = False
        aam.USEBANGUMIAPI = True
        aam.USETMDBAPI = False
        with patch.object(
            aam,
            "Auxiliary_Http",
            return_value={"list": [{"name_cn": "葬送的芙莉莲", "name": "Frieren"}]},
        ):
            result = aam.Auxiliary_Api("Frieren")
        self.assertEqual(result, "葬送的芙莉莲")

    def test_alias_key_normalizes_punctuation_variants_to_same_canonical_title(self):
        aam.Auxiliary_UpsertCanonicalTitle(
            "命运:奇异赝品",
            "Fate/Strange Fake",
            "Fate strange Fake",
            "Bangumi",
            ["Fate：Strange Fake"],
        )
        canonical_zh, canonical_id, _ = aam.Auxiliary_ResolveCanonicalTitleByAliases("Fate：Strange Fake")
        self.assertEqual(canonical_zh, "命运：奇异赝品")
        self.assertIsNotNone(canonical_id)
        self.assertEqual(
            aam.Auxiliary_NormalizeAliasKey("Fate：Strange Fake"),
            aam.Auxiliary_NormalizeAliasKey("Fate/Strange Fake"),
        )

    def test_alias_key_normalizes_ordinal_season_variant(self):
        self.assertEqual(
            aam.Auxiliary_NormalizeAliasKey("Medalist 2nd Season"),
            aam.Auxiliary_NormalizeAliasKey("Medalist Season 2"),
        )

    def test_openai_identify_user_message_strips_leading_bracket_release_tag(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "测",
                                        "anime_name_en": "Otaku ni Yasashii Gal wa Inai",
                                        "anime_name_romaji": "X",
                                        "season": "1",
                                        "episode": "3",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        file_name = "[Tsukigakirei] Otaku ni Yasashii Gal wa Inai - 03.mp4"
        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        with patch.object(aam, "post", return_value=DummyResponse()) as mock_post:
            aam.Auxiliary_OpenAIIdentifyFileInfo(file_name)
        payload = mock_post.call_args[1]["json"]
        user_content = payload["messages"][-1]["content"]
        self.assertTrue(
            user_content.startswith("Otaku"),
            msg=f"expected stripped prompt, got: {user_content!r}",
        )
        self.assertNotIn("[Tsukigakirei]", user_content)

    def test_openai_full_info_reuses_history_chinese_by_english_or_romaji(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "",
                                        "anime_name_en": "Sousou no Frieren",
                                        "anime_name_romaji": "Sousou no Frieren",
                                        "season": "2",
                                        "episode": "8",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        aam.Auxiliary_UpsertCanonicalTitle(
            "葬送的芙莉莲",
            "Sousou no Frieren",
            "Sousou no Frieren",
            "Bangumi",
            ["Frieren"],
        )
        with patch.object(aam, "post", return_value=DummyResponse()):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo("[Test] Sousou no Frieren - 08.mkv")
        self.assertEqual(result, ("02", "08", "2", "8", "葬送的芙莉莲"))
        self.assertEqual(aam.LastOpenAIFileInfoMeta.get("CanonicalZh"), "葬送的芙莉莲")
        self.assertEqual(aam.LastOpenAIFileInfoMeta.get("NameEN"), "Sousou no Frieren")

    def test_openai_full_info_prefers_tmdb_chinese_over_ai_chinese(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "葬送的芙莉莲(旧译名)",
                                        "anime_name_en": "Sousou no Frieren",
                                        "anime_name_romaji": "Sousou no Frieren",
                                        "season": "1",
                                        "episode": "8",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        with patch.object(aam, "post", return_value=DummyResponse()), patch.object(
            aam, "Auxiliary_QueryBangumiChineseTitle", return_value=None
        ), patch.object(
            aam, "Auxiliary_QueryTMDBChineseTitle", return_value="葬送的芙莉莲"
        ):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo("[Test] Sousou no Frieren - 08.mkv")
        self.assertEqual(result, ("01", "08", "1", "8", "葬送的芙莉莲"))

    def test_openai_full_info_uses_ai_chinese_when_tmdb_not_hit(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "葬送的芙莉莲",
                                        "anime_name_en": "Sousou no Frieren",
                                        "anime_name_romaji": "Sousou no Frieren",
                                        "season": "1",
                                        "episode": "9",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        with patch.object(aam, "post", return_value=DummyResponse()), patch.object(
            aam, "Auxiliary_QueryBangumiChineseTitle", return_value=None
        ), patch.object(
            aam, "Auxiliary_QueryTMDBChineseTitle", return_value=None
        ):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo("[Test] Sousou no Frieren - 09.mkv")
        self.assertEqual(result, ("01", "09", "1", "9", "葬送的芙莉莲"))

    def test_openai_full_info_uses_bangumi_chinese_when_ai_chinese_empty(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "",
                                        "anime_name_en": "Gnosia",
                                        "anime_name_romaji": "Gnosia",
                                        "season": "1",
                                        "episode": "5",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        with patch.object(aam, "post", return_value=DummyResponse()), patch.object(
            aam, "Auxiliary_QueryBangumiChineseTitle", return_value="诺希亚"
        ) as mocked_bangumi, patch.object(
            aam, "Auxiliary_QueryTMDBChineseTitle", return_value=None
        ):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo("[LoliHouse] GNOSIA - 05.mkv")
        self.assertEqual(result[0:4], ("01", "05", "1", "5"))
        self.assertTrue(aam.Auxiliary_HasChineseText(result[4]))
        mocked_bangumi.assert_called()

    def test_openai_identify_normalizes_decimal_episode_without_forcing_special(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "地底奴隶的驯兽师",
                                        "anime_name_en": "Mato Seihei no Slave 2",
                                        "anime_name_romaji": "Mato Seihei no Slave 2",
                                        "season": "2",
                                        "episode": "6.0",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        with patch.object(aam, "post", return_value=DummyResponse()), patch.object(
            aam, "Auxiliary_QueryBangumiChineseTitle", return_value=None
        ), patch.object(
            aam, "Auxiliary_QueryTMDBChineseTitle", return_value=None
        ):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo("[LoliHouse] Mato Seihei no Slave 2 - 06.mkv")
        self.assertEqual(result[0:4], ("02", "06", "2", "6"))
        self.assertTrue(result[4] not in [None, ""])

    def test_openai_identify_prefers_hint_canonical_when_single_episode_drift(self):
        class DummyResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "anime_name_zh": "弹丸论破:希望的学园与绝望高中生",
                                        "anime_name_en": "Danganronpa: The Animation",
                                        "anime_name_romaji": "Danganronpa: The Animation",
                                        "season": "1",
                                        "episode": "6",
                                        "special": False,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }

        aam.OPENAI_IDENTIFY_ALL = True
        aam.OPENAI_API_KEY = "test-key"
        gnosia_cid, _ = aam.Auxiliary_UpsertCanonicalTitle(
            "吉诺西亚",
            "GNOSIA",
            "Gnosia",
            "legacy",
            ["Gnosia"],
        )
        file_name = "[LoliHouse] GNOSIA - 06 [WebRip].mkv"
        aam.OpenAIIdentifyFileMemoryCache.pop(file_name, None)
        with patch.object(aam, "post", return_value=DummyResponse()), patch.object(
            aam, "Auxiliary_QueryBangumiChineseTitle", return_value=None
        ), patch.object(
            aam, "Auxiliary_QueryTMDBChineseTitle", return_value=None
        ), patch.object(
            aam,
            "Auxiliary_PreDetectEpisodeHint",
            return_value={"CanonicalID": gnosia_cid, "EpisodeKey": "stub"},
        ):
            result = aam.Auxiliary_OpenAIIdentifyFileInfo(file_name)
        self.assertEqual(result[0:4], ("01", "06", "1", "6"))
        self.assertNotIn("弹丸", result[4])
        self.assertTrue(aam.Auxiliary_HasChineseText(result[4]))

    def test_processing_main_openai_non_chinese_title_keeps_current_name_without_api_fallback(self):
        file_name = "GNOSIA - 01.mkv"

        def fake_ident(_):
            aam.LastIdentificationFromAI = True
            aam.LastOpenAIFileInfoMeta = {
                "NameEN": "GNOSIA",
                "NameRomaji": "Gnosia",
                "CanonicalID": "gnosia_test_id",
                "CanonicalZh": "GNOSIA",
            }
            return ("01", "01", "1", "1", "GNOSIA")

        with patch.object(aam, "Processing_Identification", side_effect=fake_ident), patch.object(
            aam, "Auxiliary_Api", return_value="诺希亚"
        ) as mocked_api, patch.object(
            aam, "Sorting_Mv", return_value={"status": "success", "message": "", "dst": "dst", "src": "src"}
        ) as mocked_sort:
            aam.Processing_Main([file_name])

        mocked_api.assert_not_called()
        self.assertEqual(mocked_sort.call_count, 1)

    def test_processing_identification_skips_and_logs_when_openai_identify_fails(self):
        warn_path = self.tmp_path / "logs" / "AutoAnime_openai_identify_warnings.json"
        with patch.object(aam, "Auxiliary_OpenAIIdentifyFileInfo", return_value=None):
            result = aam.Processing_Identification("Fallback Anime - 01.mkv")
        self.assertIsNone(result)
        self.assertTrue(warn_path.is_file())
        payload = json.loads(warn_path.read_text(encoding="utf-8"))
        self.assertIn("records", payload)
        self.assertTrue(len(payload["records"]) >= 1)
        last = payload["records"][-1]
        self.assertIn(last.get("reason", ""), ("openai_identify_returned_none",))

    def test_openai_coalesce_episode_accepts_integer_zero(self):
        self.assertEqual(aam.Auxiliary_CoalesceEpisodeFromParsed({"episode": 0}), "0")
        self.assertEqual(aam.Auxiliary_CoalesceEpisodeFromParsed({"ep": 0}), "0")
        self.assertEqual(aam.Auxiliary_CoalesceSeasonFromParsed({"season": 0}), "0")

    def test_processing_main_keeps_oldest_file_for_same_episode(self):
        old_name = "TestAnime =01= old.mkv"
        new_name = "TestAnime =01= new.mkv"
        old_file = self.tmp_path / old_name
        new_file = self.tmp_path / new_name
        old_file.write_text("old", encoding="utf-8")
        new_file.write_text("new", encoding="utf-8")
        now = time.time()
        os.utime(old_file, (now - 200, now - 200))
        os.utime(new_file, (now - 10, now - 10))

        aam.DRY_RUN = False
        aam.NAMING_STYLE = "emby"
        aam.USELINK = False
        aam.MANDATORYCOVER = True
        aam.Auxiliary_InitRuntimeContext()

        def fake_ident(path):
            aam.LastIdentificationFromAI = True
            aam.LastOpenAIFileInfoMeta = {
                "NameEN": "",
                "NameRomaji": "",
                "CanonicalID": "testanime_id",
                "CanonicalZh": "测试番剧",
            }
            return ("01", "01", "1", "1", "TestAnime")

        with patch.object(aam, "Processing_Identification", side_effect=fake_ident) as mocked_ident:
            aam.Processing_Main([new_name, old_name])

        expected_target = self.tmp_path / "测试番剧" / "Season 01" / "测试番剧 - S01E01.mkv"
        self.assertTrue(expected_target.exists())
        self.assertFalse(old_file.exists())
        self.assertTrue(new_file.exists())
        self.assertEqual(mocked_ident.call_count, 2)
        self.assertEqual(aam.Runtime.operation_records[-1]["status"], "skipped")
        self.assertEqual(aam.Runtime.operation_records[-1]["message"], "already_organized_show_cache")

    def test_jujutsu_openai_cache_record_contracts_title_and_absolute_episode(self):
        record = {
            "SE": "01",
            "EP": "57",
            "RAWSE": "1",
            "RAWEP": "57",
            "RAWName": "咒术回战 怀玉･玉折 / 涩谷事变",
            "NameEN": "Jujutsu Kaisen",
            "NameRomaji": "Jujutsu Kaisen",
            "CanonicalID": "",
        }
        with patch.object(
            aam,
            "Auxiliary_RemappedJujutsuKaisenSeasonEpisode",
            return_value=("3", "10", "03", "10"),
        ):
            fixed, updated = aam.Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(record.copy())
        self.assertTrue(updated)
        self.assertEqual(fixed["RAWName"], "咒术回战")
        self.assertEqual(fixed["RAWSE"], "3")
        self.assertEqual(fixed["RAWEP"], "10")
        self.assertEqual(fixed["SE"], "03")
        self.assertEqual(fixed["EP"], "10")

    def test_normalize_display_title_uses_fullwidth_question_mark(self):
        self.assertIn("？", aam.Auxiliary_NormalizeDisplayTitle("abc?"))

    def test_sanitize_path_does_not_turn_question_into_trailing_underscore(self):
        name = aam.Auxiliary_SanitizePathComponent("多闻君现在是哪边？")
        self.assertIn("？", name)
        self.assertFalse(name.endswith("_"))

    def test_sanitize_path_preserves_fullwidth_quotes_in_chinese_title(self):
        name = aam.Auxiliary_SanitizePathComponent("公主殿下，“拷问”的时间到了")
        self.assertIn("\u201c", name)
        self.assertIn("\u201d", name)
        self.assertNotIn("公主殿下,_", name)

    def test_tmdb_season_layout_parse_and_absolute_map(self):
        details = {
            "seasons": [
                {"season_number": 0, "episode_count": 3},
                {"season_number": 1, "episode_count": 24},
                {"season_number": 2, "episode_count": 23},
                {"season_number": 3, "episode_count": 12},
            ]
        }
        pairs = aam.Auxiliary_ParseTMDBTvDetailsSeasonLayout(details)
        self.assertEqual(pairs, [(1, 24), (2, 23), (3, 12)])
        self.assertEqual(aam.Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(24, pairs), (1, 24))
        self.assertEqual(aam.Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(25, pairs), (2, 1))
        self.assertEqual(aam.Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(57, pairs), (3, 10))
        self.assertEqual(aam.Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(70, pairs), (3, 23))

    def test_jujutsu_remap_uses_tmdb_layout_when_available(self):
        layout = [(1, 10), (2, 5)]
        with patch.object(aam, "Auxiliary_ResolveTMDBTvIdForJujutsuKaisen", return_value=999), patch.object(
            aam, "Auxiliary_GetTMDBTvSeasonLayoutBySeriesId", return_value=layout
        ):
            out = aam.Auxiliary_RemappedJujutsuKaisenSeasonEpisode(
                "1", "12", "01", "12", "Jujutsu Kaisen", "Jujutsu Kaisen", "咒术回战"
            )
        self.assertEqual(out[0:2], ("2", "2"))
