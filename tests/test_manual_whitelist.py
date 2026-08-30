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

    def test_manual_whitelist_covers_new_titles(self):
        """新增高频/低频英文罗马音与中文别译兜底映射。"""
        cases = [
            # 高频英文/罗马音
            ('Isekai Nonbiri Nouka', '异世界悠闲农家'),
            ('Isekai Nonbiri Nouka 2nd Season', '异世界悠闲农家'),
            ('Maid-san wa Taberu Dake', '女仆小姐的贪吃日常'),
            ('Otonari no Tenshi-sama', '关于邻家的天使大人不知不觉把我惯成了废人这档子事'),
            ('Otonari no Tenshi-sama 2', '关于邻家的天使大人不知不觉把我惯成了废人这档子事'),
            ('Otonari no Tenshi-sama ni Itsunomanika Dame Ningen ni Sareteita Ken', '关于邻家的天使大人不知不觉把我惯成了废人这档子事'),
            ('Otonari no Tenshi-sama ni Itsunomanika Dame Ningen ni Sareteita Ken 2', '关于邻家的天使大人不知不觉把我惯成了废人这档子事'),
            ('Aishiteru Game wo Owarasetai', '想结束这场“我爱你”的游戏'),
            ('Ookii Onnanoko wa Suki Desuka', '你喜欢高大的女孩子吗？'),
            ('Ikoku Nikki', '异国日记'),
            ('Ichijyoma Mankitsu Gurashi', '一叠间漫画咖啡厅日常'),
            ('Arne no Jikenbo', '阿涅斯事件簿'),
            ('Akane-banashi', '落语朱音'),
            ('Chitose-kun wa Ramune Bin no Naka', '千岁君在波子汽水瓶中'),
            ('Saikyou no Ousama, Nidome no Jinsei wa Nani o Suru', '最强王者的第二人生'),
            ('Saikyou no Ousama, Nidome no Jinsei wa Nani wo Suru', '最强王者的第二人生'),
            ('终末起点', '最强王者的第二人生'),
            # 用户指定作品
            ('Otaku ni Yasashii Gal wa Inai', '哪里有温柔对待阿宅的辣妹！？'),
            ('哪里有温柔对待阿宅的辣妹', '哪里有温柔对待阿宅的辣妹！？'),
            ('没有辣妹会对阿宅温柔', '哪里有温柔对待阿宅的辣妹！？'),
            # 低频/剩余
            ('VIRGIN PUNK', '处女朋克'),
            ('Odayaka Kizoku no Kyuuka no Susume', '优雅贵族的休假指南'),
            ('Kuranika', '和班上第二可爱的女孩子成了朋友'),
            ('Kirei ni Shite Moraemasu ka', '能帮我弄干净吗'),
            ('Champignon no Majo', '蘑菇魔女'),
            ('Bungou Stray Dogs Wan', '文豪野犬 汪！'),
            ('Otome Kaijuu Carameliser', '乙女怪兽卡列尼策'),
            ('NEEDY GIRL OVERDOSE', '主播女孩重度依赖'),
            ('Yuusha Kei ni Shosu Choubatsu Yuusha 9004-tai Keimu Kiroku', '判处勇者刑 惩罚勇者9004队刑务纪录'),
        ]
        for raw_name, expected_zh in cases:
            with self.subTest(raw_name=raw_name):
                result = Auxiliary_GetManualWhitelistedTitle(raw_name)
                self.assertEqual(
                    result,
                    expected_zh,
                    f'{raw_name} 应命中白名单中文名 {expected_zh}，实际得到 {result}',
                )
    def test_manual_whitelist_covers_remaining_english_dirs(self):
        """round2 真实数据回归后仍未收敛的英文目录兜底映射。"""
        cases = [
            ('Youkoso Jitsuryoku Shijou Shugi no Kyoushitsu e', '欢迎来到实力至上主义的教室'),
            ('Tongari Boushi no Atelier', '尖帽子的魔法工坊'),
            ('Ace of Diamond Act II', '钻石王牌 act2'),
            ('Kuroneko to Majo no Kyoushitsu', '黑猫与魔女的教室'),
            ('Kanan-sama wa Akumade Choroi', '迦楠大人的白给是恶魔级'),
            ('Mamonogurai no Boukensha', '吞噬魔物的冒险者'),
            ('Class de 2-banme ni Kawaii Onnanoko to Tomodachi ni Natta', '和班上第二可爱的女孩子成了朋友'),
            ('Honzuki no Gekokujou', '小书痴的下克上：为了成为图书管理员不择手段！'),
            ('Ponkotsu Fuuki Iin to Skirt-take ga Futekisetsu na JK no Hanashi', '木头风纪委员和迷你裙JK的故事'),
            ('Yuusha no Rokkotsu de', '女神“异世界转生想成为什么”我“勇者的肋骨”'),
            ('Tsue to Tsurugi no Wistoria', '杖与剑的魔剑谭'),
            ('Awajima Hyakkei', '淡岛百景'),
            ('Yozakura-san Chi no Daisakusen', '夜樱家的大作战'),
            ('Himekishi wa Barbaroi no Yome', '女骑士成为蛮族新娘'),
            ('Dorohedoro', '异兽魔都'),
            ('Re Zero kara Hajimeru Isekai Seikatsu', 'Re：从零开始的异世界生活'),
            ('Kamiina Botan, Yoeru Sugata wa Yuri no Hana', '上伊那牡丹，酒醉身姿似百合花般'),
            ('Shunkashuutou Daikousha - Haru no Mai', '春夏秋冬代行者 春之舞'),
            ('Marika-chan no Koukando wa Bukkowareteiru', '茉莉花同学的好感度坏得很彻底'),
            ('Niwatori Fighter', '公鸡斗士'),
            ('Kanojo Okarishimasu', '租借女友'),
            ('Kabushikigaisha-Magi-Lumi=re', '魔法光源股份有限公司'),
            ('Gaikotsu Kishi-sama, Tadaima Isekai e Odekakechuu II', '骸骨骑士大人异世界冒险中'),
            ('Toukutsu Ou', '最强王图鉴 ～The Ultimate Battles～'),
            ('Tomb Raider King', '最强王图鉴 ～The Ultimate Battles～'),
            ('盜墓王', '最强王图鉴 ～The Ultimate Battles～'),
            ('BLEACH Sennen Kessen-hen', '死神 千年血战篇-祸进谭'),
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

    def test_bare_bleach_is_not_whitelisted_as_tybw(self):
        """裸 Bleach 不得命中千年血战篇，否则原作会被整进祸进谭。"""
        self.assertIsNone(Auxiliary_GetManualWhitelistedTitle('Bleach'))
        self.assertIsNone(Auxiliary_GetManualWhitelistedTitle('BLEACH'))
        self.assertEqual(
            Auxiliary_GetManualWhitelistedTitle('BLEACH Sennen Kessen-hen'),
            '死神 千年血战篇-祸进谭',
        )


if __name__ == '__main__':
    unittest.main()
