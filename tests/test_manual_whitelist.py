# -*- coding: utf-8 -*-
"""手工剧名白名单兜底测试（第 4.2 单元）。"""

import unittest
from unittest.mock import patch

from autoanime import state
from autoanime.cache.manual_whitelist import (
    Auxiliary_GetManualWhitelistedTitle,
    Auxiliary_LoadManualWhitelist,
)
from autoanime.config_loader import Auxiliary_InitRuntimeContext


class TestManualWhitelist(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state.init_defaults()
        state.PRINTLOGFLAG = False
        state.CACHE_DIR = self.tmp.name
        Auxiliary_InitRuntimeContext()
        Auxiliary_LoadManualWhitelist(force=True)

    def test_manual_whitelist_covers_english_titles(self):
        """常见英文/罗马音剧名应能命中白名单中文标题。"""
        cases = [
            ('Fate strange Fake', '命运：奇异赝品'),
            ('Fate-strange-Fake', '命运：奇异赝品'),
            ('GANSO BanG Dream Chan', 'BanG Dream! 元祖小剧场'),
            ('GANSO-BanG-Dream-Chan', 'BanG Dream! 元祖小剧场'),
            ('Medalist', '金牌得主'),
            ('Medalist-2nd-Season', '金牌得主'),
            ('Ganbare! Nakamura-kun!!', '加油吧！中村君！！'),
            ('Ganbare-Nakamura-kun', '加油吧！中村君！！'),
            ('MAO', '摩绪'),
            ('Yuusha no Kuzu', '勇者之屑'),
            ('Mato Seihei no Slave', '魔都精兵的奴隶'),
            ('Koori no Jouheki', '冰之城墙'),
            ('Digimon Beatbreak', '数码宝贝 觉醒节拍'),
        ]
        for raw_name, expected_zh in cases:
            with self.subTest(raw_name=raw_name):
                result = Auxiliary_GetManualWhitelistedTitle(raw_name)
                self.assertEqual(
                    result,
                    expected_zh,
                    f'{raw_name} 应命中白名单中文名 {expected_zh}，实际得到 {result}',
                )

    def test_fallback_uses_manual_whitelist_when_api_fails(self):
        """Bangumi/TMDB 都失败时，回退链路应使用白名单返回中文名。"""
        from autoanime.identification.local_fallback import Auxiliary_FallbackTraditionalApis

        with patch('autoanime.apis.bgm.Auxiliary_QueryBgmChineseTitle', return_value=None) as mock_bgm, \
             patch('autoanime.apis.bangumi.Auxiliary_QueryBangumiChineseTitle', return_value=None) as mock_bangumi, \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBEnglishTitle', return_value=None):
            result = Auxiliary_FallbackTraditionalApis(('01', '13', '1', '13', 'Fate strange Fake'))

        self.assertIsNotNone(result)
        self.assertEqual(result[4], '命运：奇异赝品')
        # API 被调过但都未命中
        self.assertTrue(mock_bgm.called or mock_bangumi.called)


if __name__ == '__main__':
    unittest.main()
