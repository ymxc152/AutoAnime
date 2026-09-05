# -*- coding: utf-8 -*-
"""用户指定格式集成测试：API 全失败时仍能命中白名单。"""

import unittest
from unittest.mock import patch

from autoanime import state
from autoanime.cache.manual_whitelist import Auxiliary_LoadManualWhitelist
from autoanime.config_loader import Auxiliary_InitRuntimeContext
from autoanime.identification.local_fallback import Auxiliary_ResolveFileInfoWithFallback
from autoanime.naming import Auxiliary_IDEEP, Auxiliary_IDEVDName, Auxiliary_RMOTSTR, Auxiliary_RMSubtitlingTeam, Auxiliary_UniformOTSTR


class TestLocalFallbackSpecificFormats(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state.init_defaults()
        state.PRINTLOGFLAG = False
        state.CACHE_DIR = self.tmp.name
        Auxiliary_InitRuntimeContext()
        Auxiliary_LoadManualWhitelist(force=True)

    def test_user_specified_formats_resolve_to_canonical_zh(self):
        """两种用户指定文件名应统一收敛到同一中文剧名。"""
        files = [
            '六四位元字幕組★哪裡有溫柔對待阿宅的辣妹！？ Otaku ni Yasashii Gal wa Inai★09★1920x1080★AVC AAC MP4★繁體中文[ 組慶四週年]',
            '【今晚月色真美】[没有辣妹会对阿宅温柔！？ / オタクに優しいギャルはいない!? / Otaku ni Yasashii Gal wa Inai!?][11][WEBrip][1080P][简日内嵌]',
        ]
        expected = '哪里有温柔对待阿宅的辣妹！？'
        with patch('autoanime.apis.bgm.Auxiliary_QueryBgmChineseTitle', return_value=None), \
             patch('autoanime.apis.bangumi.Auxiliary_QueryBangumiChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBEnglishTitle', return_value=None):
            for file_name in files:
                with self.subTest(file_name=file_name):
                    info, meta = Auxiliary_ResolveFileInfoWithFallback(file_name)
                    self.assertIsNotNone(info, f'{file_name} 应能被识别')
                    self.assertEqual(info[4], expected, f'{file_name} 最终剧名不匹配')
                    self.assertEqual(meta.get('CanonicalZh'), expected, f'{file_name} CanonicalZh 不匹配')

    def test_sxxexx_format_recognized_when_apis_fail(self):
        """SxxExx 格式在 API 全失败时应能被本地规则识别出季/集/剧名。"""
        cases = [
            (
                'Skeleton.Knight.in.Another.World.S02E01.1080p.friDay.WEB-DL.AAC2.0.H.264-MWeb.mkv',
                '02', '01', '2', '01', 'Skeleton.Knight.in.Another.World',
            ),
            (
                'Chainsmoker.Cat.S01E01.1080p.NF.WEB-DL.AAC2.0.H.264-VARYG.mkv',
                '01', '01', '1', '01', 'Chainsmoker.Cat',
            ),
        ]
        with patch('autoanime.apis.bgm.Auxiliary_QueryBgmChineseTitle', return_value=None), \
             patch('autoanime.apis.bangumi.Auxiliary_QueryBangumiChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBEnglishTitle', return_value=None):
            for file_name, se, ep, rawse, rawep, rawname in cases:
                with self.subTest(file_name=file_name):
                    info, meta = Auxiliary_ResolveFileInfoWithFallback(file_name)
                    self.assertIsNotNone(info, f'{file_name} 应能被识别')
                    self.assertEqual(info[0], se, f'{file_name} 季号不匹配')
                    self.assertEqual(info[1], ep, f'{file_name} 集号不匹配')
                    self.assertEqual(info[2], rawse, f'{file_name} 原始季号不匹配')
                    self.assertEqual(info[3], rawep, f'{file_name} 原始集号不匹配')
                    self.assertEqual(info[4], rawname, f'{file_name} 剧名不匹配')
                    self.assertEqual(meta.get('Source'), 'local_rules_only')

    def test_ideep_recognizes_sxxexx_variants(self):
        """Auxiliary_IDEEP 应能识别各种大小写的 SxxExx / Exx 格式。"""
        cases = [
            ('Skeleton.Knight.in.Another.World.S02E01...mkv', '01'),
            ('Show.S02E01.mkv', '01'),
            ('Show.s02e01.mkv', '01'),
            ('Show.S02E12.mkv', '12'),
        ]
        for raw_file, expected_ep in cases:
            with self.subTest(raw_file=raw_file):
                file = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(raw_file)))
                self.assertEqual(Auxiliary_IDEEP(file), expected_ep)

    def test_idevdname_trims_sxxexx_marker(self):
        """Auxiliary_IDEVDName 对 SxxExx 格式应截断出干净剧名。"""
        raw_file = 'Skeleton.Knight.in.Another.World.S02E01.1080p.friDay.WEB-DL.AAC2.0.H.264-MWeb.mkv'
        file = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(raw_file)))
        self.assertEqual(Auxiliary_IDEVDName(file, '01'), 'Skeleton.Knight.in.Another.World')


if __name__ == '__main__':
    unittest.main()
