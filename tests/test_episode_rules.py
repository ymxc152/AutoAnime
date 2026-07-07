# -*- coding: utf-8 -*-
"""剧集规则 / 绝对集数映射专项测试。"""

import unittest

from autoanime import state
from autoanime.identification.episode_rules import (
    Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode,
)


class TestEpisodeRules(unittest.TestCase):
    def setUp(self):
        state.init_defaults()
        state.PRINTLOGFLAG = False
        state.SEEPSINGLECHARACTER = False

    def test_frieren_absolute_episode_maps_to_season_two(self):
        """葬送的芙莉莲 S1=28 集，绝对集号 38 应映射为 S02E10。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '38', '01', '38',
            'Sousou no Frieren', 'Sousou no Frieren', '葬送的芙莉莲',
            SeasonPairs=[(1, 28)],
        )
        self.assertEqual(result, ('2', '10', '02', '10'))

    def test_frieren_within_first_season_not_remapped(self):
        """葬送的芙莉莲 EP=10 落在 S1 范围内，不应映射。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '10', '01', '10',
            'Sousou no Frieren', 'Sousou no Frieren', '葬送的芙莉莲',
            SeasonPairs=[(1, 28)],
        )
        self.assertIsNone(result)

    def test_frieren_explicit_season_two_not_remapped(self):
        """文件名已显式指定 S2 时，信任原季号，不做绝对集数映射。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '2', '10', '02', '10',
            'Sousou no Frieren', 'Sousou no Frieren', '葬送的芙莉莲',
            SeasonPairs=[(1, 28)],
        )
        self.assertIsNone(result)

    def test_absolute_episode_mapping_for_jujutsu_kaisen(self):
        """咒术回战原有绝对集数映射仍正确：25->S02E01, 47->S02E23, 48->S03E01。"""
        # S1 末尾，不映射
        self.assertIsNone(
            Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
                '1', '24', '01', '24',
                'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
                SeasonPairs=[(1, 24), (2, 23), (3, 14)],
            )
        )
        # S2 开头
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '25', '01', '25',
            'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
            SeasonPairs=[(1, 24), (2, 23), (3, 14)],
        )
        self.assertEqual(result, ('2', '1', '02', '01'))
        # S2 末尾
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '47', '01', '47',
            'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
            SeasonPairs=[(1, 24), (2, 23), (3, 14)],
        )
        self.assertEqual(result, ('2', '23', '02', '23'))
        # S3 开头
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '48', '01', '48',
            'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
            SeasonPairs=[(1, 24), (2, 23), (3, 14)],
        )
        self.assertEqual(result, ('3', '1', '03', '01'))

    def test_absolute_episode_out_of_range_warns(self):
        """地狱乐 EP=25 超出正片范围（S1=13），应放弃映射并记录 WARNING。"""
        state.LogData = ''
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '25', '01', '25',
            'Jigokuraku', 'Jigokuraku', '地狱乐',
        )
        self.assertIsNone(result)
        self.assertIn('WARNING', state.LogData)
        self.assertIn('超出已知正片范围', state.LogData)

    def test_manual_lookup_by_normalized_alias(self):
        """内置表应支持英文/罗马音/大小写变体命中。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '38', '01', '38',
            'Sousou no Frieren', 'Sousou no Frieren', '',
        )
        self.assertEqual(result, ('2', '10', '02', '10'))


if __name__ == '__main__':
    unittest.main()
