# -*- coding: utf-8 -*-
"""用户指定格式集成测试：API 全失败时仍能命中白名单。"""

import unittest
from unittest.mock import patch

from autoanime import state
from autoanime.cache.manual_whitelist import Auxiliary_LoadManualWhitelist
from autoanime.config_loader import Auxiliary_InitRuntimeContext
from autoanime.identification.local_fallback import Auxiliary_ResolveFileInfoWithFallback


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


if __name__ == '__main__':
    unittest.main()
