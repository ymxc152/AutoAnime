# -*- coding: utf-8 -*-
"""文本/标题清洗工具测试。"""

import unittest

from autoanime.text_utils import Auxiliary_CleanFallbackTitle


class TestCleanFallbackTitle(unittest.TestCase):
    def test_clean_fallback_title_removes_subtitle_group_prefix(self):
        """去掉开头的「字幕组」等发行方前缀，保留剧名本体。"""
        raw = '六四位元字幕組=哪有温柔对待阿宅的辣妹！？=Otaku=ni=Yasashii=Gal=wa=Inai='
        result = Auxiliary_CleanFallbackTitle(raw)
        self.assertEqual(result, '哪有温柔对待阿宅的辣妹！？')

    def test_clean_fallback_title_extracts_chinese_from_multilingual(self):
        """从 [中 / 日 / 英] 多语言格式中提取中文部分。"""
        raw = '=没有辣妹会对阿宅温柔！？=/=オタクに優しいギャルはいない！？=/=Otaku=ni=Yasashii=Gal=wa=Inai！？='
        result = Auxiliary_CleanFallbackTitle(raw)
        self.assertEqual(result, '没有辣妹会对阿宅温柔！？')

    def test_clean_fallback_title_removes_trailing_non_chinese(self):
        """去掉尾部英文/日文罗马音等非中文字符串。"""
        raw = '哪有温柔对待阿宅的辣妹！？=Otaku=ni=Yasashii=Gal=wa=Inai='
        result = Auxiliary_CleanFallbackTitle(raw)
        self.assertEqual(result, '哪有温柔对待阿宅的辣妹！？')

    def test_clean_fallback_title_passes_through_non_chinese(self):
        """不含中文的标题原样返回，避免误伤纯英文标题。"""
        raw = 'Otaku ni Yasashii Gal wa Inai'
        result = Auxiliary_CleanFallbackTitle(raw)
        self.assertEqual(result, raw)


if __name__ == '__main__':
    unittest.main()
