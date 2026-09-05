# -*- coding: utf-8 -*-
"""回退链路 / 剧名漂移保护专项测试。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autoanime import state
from autoanime.cache.canonical import Auxiliary_GetCanonicalTitleRecord
from autoanime.cache.show_index import Auxiliary_FormatOrganizedEpisodeTag
from autoanime.pipeline.main import Processing_Main


class TestFallbackIdentification(unittest.TestCase):
    def setUp(self):
        state.init_defaults()
        state.PRINTLOGFLAG = False
        state.DRY_RUN = True
        state.NAMING_STYLE = 'emby'
        state.USELINK = False
        state.MANDATORYCOVER = True
        state.CategoryName = ''
        state.animename = ''

    def _mark_episode_organized(self, canonical_id, title_zh, title_en, title_romaji, se, ep, dst_path):
        """在 ShowOrganizationIndex 中预埋一条已整理记录，并指向真实存在的文件。"""
        from autoanime.cache.show_index import Auxiliary_SetShowOrganizationRecord

        tag = Auxiliary_FormatOrganizedEpisodeTag(se, ep)
        Auxiliary_SetShowOrganizationRecord(
            canonical_id,
            {
                'canonical_id': canonical_id,
                'title_zh': title_zh,
                'title_en': title_en,
                'title_romaji': title_romaji,
                'organized_episodes': [tag],
                'episode_last_dst': {tag: str(dst_path)},
                'v': 2,
            },
        )

    def _mock_ident_for(self, file_name, raw_name, se, ep, name_en='', name_romaji='', canonical_id='', canonical_zh=''):
        """构造 Processing_Identification 的返回值与副作用。"""

        def fake_ident(_):
            state.LastIdentificationFromAI = False
            state.LastOpenAIFileInfoMeta = {
                'NameEN': name_en or raw_name,
                'NameRomaji': name_romaji or raw_name,
                'CanonicalID': canonical_id,
                'CanonicalZh': canonical_zh,
            }
            return (se, ep, se, ep, raw_name)

        return patch('autoanime.pipeline.main.Processing_Identification', side_effect=fake_ident)

    def test_drift_protection_does_not_merge_unrelated_shows(self):
        """无关番剧拥有相同 EP 时，不应被剧名漂移保护合并到已有 CanonicalID。"""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_dst = tmp_path / '葬送的芙莉莲' / 'Season 01' / '葬送的芙莉莲 - S01E01.mkv'
            existing_dst.parent.mkdir(parents=True, exist_ok=True)
            existing_dst.write_bytes(b'existing')

            self._mark_episode_organized(
                canonical_id='葬送的芙莉莲',
                title_zh='葬送的芙莉莲',
                title_en='Sousou no Frieren',
                title_romaji='Sousou no Frieren',
                se='01',
                ep='01',
                dst_path=existing_dst,
            )

            file_name = '[Subbers] MAO - 01 [1080p].mkv'
            with self._mock_ident_for(
                file_name, raw_name='MAO', se='01', ep='01', name_en='MAO', name_romaji='MAO'
            ), patch(
                'autoanime.pipeline.main.Sorting_Mv',
                return_value={'status': 'dry-run', 'dst': str(tmp_path / 'MAO' / 'Season 01' / 'MAO - S01E01.mkv')},
            ) as mock_sort:
                Processing_Main([file_name])

            # 应拒绝复用 CrossCID，而是为 MAO 新建一条已整理记录
            # Auxiliary_NormalizeAliasKey 会把 MAO 归一化为小写 canonical id
            self.assertIn('mao', state.ShowOrganizationIndexDataCache)
            mao_record = state.ShowOrganizationIndexDataCache['mao']
            self.assertIn('S01E01', mao_record['organized_episodes'])
            # 葬送的芙莉莲的记录保持不变，没有被 MAO 污染
            frieren_record = state.ShowOrganizationIndexDataCache['葬送的芙莉莲']
            self.assertEqual(frieren_record['title_zh'], '葬送的芙莉莲')
            # 因为拒绝漂移保护，所以实际执行了整理
            self.assertEqual(mock_sort.call_count, 1)

    def test_drift_protection_allowed_for_same_show_aliases(self):
        """同一番剧的不同拼写/别名应允许复用已有 CanonicalID。"""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            existing_dst = tmp_path / '葬送的芙莉莲' / 'Season 01' / '葬送的芙莉莲 - S01E01.mkv'
            existing_dst.parent.mkdir(parents=True, exist_ok=True)
            existing_dst.write_bytes(b'existing')

            # 预埋葬送的芙莉莲的 canonical 记录，其中 romaji 为 Sousou no Frieren
            state.CanonicalTitleIndexDataCache['葬送的芙莉莲'] = {
                'zh': '葬送的芙莉莲',
                'en': '',
                'romaji': 'Sousou no Frieren',
                'source': 'Bangumi',
                'last_updated': '2026-07-07 00:00:00',
                'confidence': 95,
                'locked': False,
            }
            self._mark_episode_organized(
                canonical_id='葬送的芙莉莲',
                title_zh='葬送的芙莉莲',
                title_en='',
                title_romaji='Sousou no Frieren',
                se='01',
                ep='01',
                dst_path=existing_dst,
            )

            file_name = '[Subbers] Sousou no Frieren - 01 [1080p].mkv'
            with self._mock_ident_for(
                file_name,
                raw_name='Sousou no Frieren',
                se='01',
                ep='01',
                name_en='Sousou no Frieren',
                name_romaji='Sousou no Frieren',
            ), patch(
                'autoanime.pipeline.main.Sorting_Mv',
                return_value={'status': 'dry-run', 'dst': str(existing_dst)},
            ) as mock_sort:
                Processing_Main([file_name])

            # 应复用葬送的芙莉莲，从而跳过整理
            self.assertIn('葬送的芙莉莲', state.ShowOrganizationIndexDataCache)
            frieren_record = state.ShowOrganizationIndexDataCache['葬送的芙莉莲']
            self.assertIn('S01E01', frieren_record['organized_episodes'])
            # 同一番剧不同拼写不应触发二次整理
            self.assertEqual(mock_sort.call_count, 0)


    def test_fallback_strips_season_suffix_before_bangumi_query(self):
        """回退链路应在查询 Bangumi 前剥离 2nd Season / S2 / Season X 等季号后缀。"""
        from autoanime.identification.local_fallback import Auxiliary_FallbackTraditionalApis

        with patch('autoanime.apis.bgm.Auxiliary_QueryBangumiChineseTitle') as mock_bgm_bangumi, \
             patch('autoanime.apis.bangumi.Auxiliary_QueryBangumiChineseTitle') as mock_bangumi, \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBEnglishTitle', return_value=None):
            mock_bgm_bangumi.return_value = None
            mock_bangumi.return_value = '金牌得主'
            result = Auxiliary_FallbackTraditionalApis(('02', '07', '2', '07', 'Medalist 2nd Season'))

        self.assertIsNotNone(result)
        self.assertEqual(result[4], '金牌得主')
        # 验证查询列表中出现了剥离季号后的名称，且优先于原始名称被查询
        all_calls = list(mock_bgm_bangumi.call_args_list) + list(mock_bangumi.call_args_list)
        queried_names = [call[0][0] for call in all_calls]
        self.assertIn('Medalist', queried_names)
        # 剥离后的名称应排在原始名称之前（先尝试更简洁的查询）
        if 'Medalist 2nd Season' in queried_names:
            self.assertLess(
                queried_names.index('Medalist'),
                queried_names.index('Medalist 2nd Season'),
            )

    def test_fallback_strips_hyphenated_season_suffix(self):
        """归一化后的 "Medalist-2nd-Season" 也应被剥离为 "Medalist" 并优先查询。"""
        from autoanime.identification.local_fallback import Auxiliary_FallbackTraditionalApis

        with patch('autoanime.apis.bgm.Auxiliary_QueryBangumiChineseTitle') as mock_bgm_bangumi, \
             patch('autoanime.apis.bangumi.Auxiliary_QueryBangumiChineseTitle') as mock_bangumi, \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBChineseTitle', return_value=None), \
             patch('autoanime.apis.tmdb.Auxiliary_QueryTMDBEnglishTitle', return_value=None):
            mock_bgm_bangumi.return_value = None
            mock_bangumi.return_value = '金牌得主'
            result = Auxiliary_FallbackTraditionalApis(('02', '07', '2', '07', 'Medalist-2nd-Season'))

        self.assertIsNotNone(result)
        all_calls = list(mock_bgm_bangumi.call_args_list) + list(mock_bangumi.call_args_list)
        queried_names = [call[0][0] for call in all_calls]
        self.assertIn('Medalist', queried_names)
        # 剥离后的名称应排在原始名称之前（先尝试更简洁的查询）
        if 'Medalist-2nd-Season' in queried_names:
            self.assertLess(
                queried_names.index('Medalist'),
                queried_names.index('Medalist-2nd-Season'),
            )


if __name__ == '__main__':
    unittest.main()
