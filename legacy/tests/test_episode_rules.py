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
        """葬送的芙莉莲 S1=30 集，绝对集号 38 应映射为 S02E08。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '38', '01', '38',
            'Sousou no Frieren', 'Sousou no Frieren', '葬送的芙莉莲',
            SeasonPairs=[(1, 30)],
        )
        self.assertEqual(result, ('2', '8', '02', '08'))

    def test_frieren_within_first_season_not_remapped(self):
        """葬送的芙莉莲 EP=10 落在 S1 范围内，不应映射。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '10', '01', '10',
            'Sousou no Frieren', 'Sousou no Frieren', '葬送的芙莉莲',
            SeasonPairs=[(1, 30)],
        )
        self.assertIsNone(result)

    def test_frieren_explicit_season_two_not_remapped(self):
        """文件名已显式指定 S2 时，信任原季号，不做绝对集数映射。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '2', '10', '02', '10',
            'Sousou no Frieren', 'Sousou no Frieren', '葬送的芙莉莲',
            SeasonPairs=[(1, 30)],
        )
        self.assertIsNone(result)

    def test_absolute_episode_mapping_for_jujutsu_kaisen(self):
        """咒术回战按扩展后的 30 集/季：30->S01E30, 31->S02E01, 60->S02E30, 61->S03E01。"""
        # S1 末尾，不映射
        self.assertIsNone(
            Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
                '1', '30', '01', '30',
                'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
                SeasonPairs=[(1, 30), (2, 30), (3, 30)],
            )
        )
        # S2 开头
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '31', '01', '31',
            'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
            SeasonPairs=[(1, 30), (2, 30), (3, 30)],
        )
        self.assertEqual(result, ('2', '1', '02', '01'))
        # S2 末尾
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '60', '01', '60',
            'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
            SeasonPairs=[(1, 30), (2, 30), (3, 30)],
        )
        self.assertEqual(result, ('2', '30', '02', '30'))
        # S3 开头
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '61', '01', '61',
            'Jujutsu Kaisen', 'Jujutsu Kaisen', '咒术回战',
            SeasonPairs=[(1, 30), (2, 30), (3, 30)],
        )
        self.assertEqual(result, ('3', '1', '03', '01'))

    def test_absolute_episode_within_extended_range(self):
        """地狱乐内置 season layout 已扩展到 30 集，EP=25 落在首季范围内，无需映射。"""
        state.LogData = ''
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '25', '01', '25',
            'Jigokuraku', 'Jigokuraku', '地狱乐',
        )
        self.assertIsNone(result)
        self.assertNotIn('超出已知正片范围', state.LogData)

    def test_absolute_episode_out_of_extended_range_warns(self):
        """地狱乐 EP=31 超出扩展后的范围（S1=30），应放弃映射并记录 WARNING。"""
        state.LogData = ''
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '31', '01', '31',
            'Jigokuraku', 'Jigokuraku', '地狱乐',
        )
        self.assertIsNone(result)
        self.assertIn('WARNING', state.LogData)
        self.assertIn('超出已知正片范围', state.LogData)

    def test_manual_lookup_by_normalized_alias(self):
        """内置表应支持英文/罗马音/大小写变体命中；S1=30 时 EP=38 映射为 S02E08。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '38', '01', '38',
            'Sousou no Frieren', 'Sousou no Frieren', '',
        )
        self.assertEqual(result, ('2', '8', '02', '08'))

    def test_bare_bleach_does_not_use_tybw_layout(self):
        """裸 Bleach / 死神 不得套用千年血战篇 13 集季表，避免原作 S04 被改号。"""
        for en, zh in (('Bleach', '死神'), ('BLEACH', 'BLEACH')):
            with self.subTest(en=en, zh=zh):
                result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
                    '1', '44', '01', '44', en, en, zh,
                )
                self.assertIsNone(result)

    def test_tybw_absolute_episode_maps_to_season_four(self):
        """千年血战篇绝对集 45（3×13 之后）应映射为 S04E06。"""
        result = Auxiliary_RemappedAbsoluteEpisodeSeasonEpisode(
            '1', '45', '01', '45',
            'BLEACH Sennen Kessen-hen', 'BLEACH Sennen Kessen-hen',
            '死神 千年血战篇-祸进谭',
        )
        self.assertEqual(result, ('4', '6', '04', '06'))


if __name__ == '__main__':
    unittest.main()
