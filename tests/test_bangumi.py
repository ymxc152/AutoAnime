# -*- coding: utf-8 -*-
"""Bangumi API 查询策略测试。"""

import unittest
from unittest.mock import patch

from autoanime import state
from autoanime.apis.bangumi import Auxiliary_QueryBangumiChineseTitle


class TestBangumiQuery(unittest.TestCase):
    def setUp(self):
        state.init_defaults()
        state.PRINTLOGFLAG = False
        state.USEBANGUMIAPI = True

    def test_bangumi_query_uses_first_result_with_chinese_name(self):
        """max_results=5 时，应跳过无中文的条目，取第一个 name_cn 含中文的结果。"""
        mock_response = {
            'list': [
                {'name': 'Medalist', 'name_cn': ''},
                {'name': 'Medalist', 'name_cn': '金牌得主'},
                {'name': 'Medalist 2nd Season', 'name_cn': '金牌得主 第二季'},
            ]
        }

        with patch('autoanime.apis.bangumi.Auxiliary_Http', return_value=mock_response) as mock_http:
            result = Auxiliary_QueryBangumiChineseTitle('Medalist')

        self.assertEqual(result, '金牌得主')
        # 验证请求 URL 中 max_results 已放宽
        call_url = mock_http.call_args[0][0]
        self.assertIn('max_results=5', call_url)

    def test_bangumi_query_returns_none_when_no_chinese_name(self):
        """所有结果都没有中文标题时，应返回 None 并记录警告。"""
        mock_response = {
            'list': [
                {'name': 'Medalist', 'name_cn': ''},
                {'name': 'Some Other Show', 'name_cn': ''},
            ]
        }

        with patch('autoanime.apis.bangumi.Auxiliary_Http', return_value=mock_response):
            result = Auxiliary_QueryBangumiChineseTitle('Medalist')

        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
