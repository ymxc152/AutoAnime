"""
autoanime 手工剧名白名单

对应原 `AutoAnimeMv.py`:
- `Auxiliary_GetManualWhitelistPath`
- `Auxiliary_LoadManualWhitelist`
- `Auxiliary_GetManualWhitelistedTitle`
"""

import json

from pathlib import Path as PathlibPath

from .. import state
from ..config_loader import Auxiliary_GetCacheStorePath
from ..logging_utils import Auxiliary_Log
from ..text_utils import (
    Auxiliary_NormalizeAliasKey,
    Auxiliary_NormalizeApiTitle,
)


def Auxiliary_GetManualWhitelistPath() -> PathlibPath:
    CacheDirPath = Auxiliary_GetCacheStorePath().parent
    return CacheDirPath / 'manual_title_whitelist.json'


def Auxiliary_LoadManualWhitelist(force=False):
    DefaultWhitelist = {
        'mao': '摩绪',
        'fatestrangefake': '命运：奇异赝品',
        'fatestrangefakewhispersofdawn': '命运：奇异赝品 黎明低语',
        'gansobangdreamchan': 'BanG Dream! 元祖小剧场',
        'bangdreamchan': 'BanG Dream! 元祖小剧场',
        'medalist': '金牌得主',
        'ganbarenakamurakun': '加油吧！中村君！！',
        'yuushanokuzu': '勇者之屑',
        'matoseiheinoslave': '魔都精兵的奴隶',
        'koorinojouheki': '冰之城墙',
        'digimonbeatbreak': '数码宝贝 觉醒节拍',
        # 高频英文/罗马音
        'isekainonbirinouka': '异世界悠闲农家',
        'isekainonbirinouka2': '异世界悠闲农家',
        'maidsanwataberudake': '女仆小姐的贪吃日常',
        'otonarinotenshisama': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
        'otonarinotenshisama2': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
        'otonarinotenshisamaniitsunomanikadameningennisareteitaken': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
        'otonarinotenshisamaniitsunomanikadameningennisareteitaken2': '关于邻家的天使大人不知不觉把我惯成了废人这档子事',
        'aishiterugamewoowarasetai': '想结束这场“我爱你”的游戏',
        'ookiionnanokowasukidesuka': '你喜欢高大的女孩子吗？',
        'ikokunikki': '异国日记',
        'ichijyomamankitsugurashi': '一叠间漫画咖啡厅日常',
        'arnenojikenbo': '阿涅斯事件簿',
        'akanebanashi': '落语朱音',
        'chitosekunwaramunebinnonaka': '千岁君在波子汽水瓶中',
        'saikyounoousamanidomenojinseiwananiosuru': '最强王者的第二人生',
        'saikyounoousamanidomenojinseiwananiwosuru': '最强王者的第二人生',
        '终末起点': '最强王者的第二人生',
        # 用户指定作品
        'otakuniyasashiigalwainai': '哪里有温柔对待阿宅的辣妹！？',
        '哪里有温柔对待阿宅的辣妹': '哪里有温柔对待阿宅的辣妹！？',
        '没有辣妹会对阿宅温柔': '哪里有温柔对待阿宅的辣妹！？',
        # 低频/剩余英文目录
        'virginpunk': '处女朋克',
        'odayakakizokunokyuukanosusume': '优雅贵族的休假指南',
        'kuranika': '和班上第二可爱的女孩子成了朋友',
        'kireinishitemoraemasuka': '能帮我弄干净吗',
        'champignonnomajo': '蘑菇魔女',
        'bungoustraydogswan': '文豪野犬 汪！',
        'otomekaijuucarameliser': '乙女怪兽卡列尼策',
        'needygirloverdose': '主播女孩重度依赖',
        'yuushakeinishosuchoubatsuyuusha9004taikeimukiroku': '判处勇者刑 惩罚勇者9004队刑务纪录',
        # round2 真实数据回归后补充的未收敛英文目录
        'youkosojitsuryokushijoushuginokyoushitsue': '欢迎来到实力至上主义的教室',
        'tongariboushinoatelier': '尖帽子的魔法工坊',
        'aceofdiamondactii': '钻石王牌 act2',
        'kuronekotomajonokyoushitsu': '黑猫与魔女的教室',
        'kanansamawaakumadechoroi': '迦楠大人的白给是恶魔级',
        'mamonogurainoboukensha': '吞噬魔物的冒险者',
        'classde2banmenikawaiionnanokototomodachininatta': '和班上第二可爱的女孩子成了朋友',
        'honzukinogekokujou': '小书痴的下克上',
        'ponkotsufuukiiintoskirttakegafutekisetsunajknohanashi': '木头风纪委员和迷你裙JK的故事',
        'yuushanorokkotsude': '女神“异世界转生想成为什么”我“勇者的肋骨”',
        'tsuetotsuruginowistoria': '杖与剑的魔剑谭',
        'awajimahyakkei': '淡岛百景',
        'yozakurasanchinodaisakusen': '夜樱家的大作战',
        'himekishiwabarbaroinoyome': '女骑士成为蛮族新娘',
        'dorohedoro': '异兽魔都',
        'rezerokarahajimeruisekaiseikatsu': 'Re：从零开始的异世界生活',
        'kamiinabotanyoerusugatawayurinohana': '上伊那牡丹，酒醉身姿似百合花般',
        'shunkashuutoudaikoushaharunomai': '春夏秋冬代行者 春之舞',
        'marikachannokoukandowabukkowareteiru': '茉莉花同学的好感度坏得很彻底',
        'niwatorifighter': '公鸡斗士',
        'kanojookarishimasu': '租借女友',
        'kabushikigaishamagilumire': '魔法光源股份有限公司',
        'gaikotsukishisamatadaimaisekaieodekakechuuii': '骸骨骑士大人异世界冒险中',
    }
    WhitelistPath = Auxiliary_GetManualWhitelistPath()
    if WhitelistPath.exists() == False:
        try:
            with open(WhitelistPath, 'w', encoding='UTF-8') as f:
                json.dump(DefaultWhitelist, f, ensure_ascii=False, indent=2)
            state.ManualTitleWhitelistDataCache = DefaultWhitelist.copy()
            state.ManualTitleWhitelistMTime = float(WhitelistPath.stat().st_mtime)
            Auxiliary_Log(f'已创建手工白名单文件: {WhitelistPath}', 'INFO')
            return state.ManualTitleWhitelistDataCache
        except Exception as err:
            Auxiliary_Log(f'创建手工白名单文件失败，将使用内置默认值: {err}', 'WARNING')
            state.ManualTitleWhitelistDataCache = DefaultWhitelist.copy()
            state.ManualTitleWhitelistMTime = 0.0
            return state.ManualTitleWhitelistDataCache

    try:
        FileMTime = float(WhitelistPath.stat().st_mtime)
    except Exception:
        FileMTime = 0.0
    if force != True and type(state.ManualTitleWhitelistDataCache) == dict and state.ManualTitleWhitelistDataCache != {} and state.ManualTitleWhitelistMTime == FileMTime:
        return state.ManualTitleWhitelistDataCache

    try:
        with open(WhitelistPath, 'r', encoding='UTF-8') as f:
            RawData = json.load(f)
        if type(RawData) != dict:
            raise ValueError('手工白名单文件格式应为 JSON 对象')
        LoadedWhitelist = {}
        for RawAlias, RawTitle in RawData.items():
            AliasKey = Auxiliary_NormalizeAliasKey(RawAlias)
            TitleValue = Auxiliary_NormalizeApiTitle(RawTitle)
            if AliasKey in [None, ''] or TitleValue in [None, '']:
                continue
            LoadedWhitelist[AliasKey] = TitleValue
        if LoadedWhitelist == {}:
            LoadedWhitelist = DefaultWhitelist.copy()
        elif len(LoadedWhitelist) < len(DefaultWhitelist):
            # 旧白名单条目过少时自动合并内置默认条目，避免历史空文件/早期文件无法享受新兜底
            Merged = DefaultWhitelist.copy()
            Merged.update(LoadedWhitelist)
            LoadedWhitelist = Merged
            try:
                with open(WhitelistPath, 'w', encoding='UTF-8') as f:
                    json.dump(LoadedWhitelist, f, ensure_ascii=False, indent=2)
                FileMTime = float(WhitelistPath.stat().st_mtime)
                Auxiliary_Log(f'已自动扩展手工白名单文件: {WhitelistPath}', 'INFO')
            except Exception as err:
                Auxiliary_Log(f'扩展手工白名单文件失败（仍使用内存合并值）: {err}', 'WARNING')
        state.ManualTitleWhitelistDataCache = LoadedWhitelist
        state.ManualTitleWhitelistMTime = FileMTime
        return state.ManualTitleWhitelistDataCache
    except Exception as err:
        Auxiliary_Log(f'读取手工白名单文件失败，将使用内置默认值: {err}', 'WARNING')
        state.ManualTitleWhitelistDataCache = DefaultWhitelist.copy()
        state.ManualTitleWhitelistMTime = 0.0
        return state.ManualTitleWhitelistDataCache


def Auxiliary_GetManualWhitelistedTitle(*AliasCandidates):
    '''返回手工白名单中文标题（按别名归一匹配）'''
    ManualWhitelist = Auxiliary_LoadManualWhitelist()
    if type(ManualWhitelist) != dict or ManualWhitelist == {}:
        return None
    for Candidate in AliasCandidates:
        AliasKey = Auxiliary_NormalizeAliasKey(Candidate)
        if AliasKey in ManualWhitelist:
            return ManualWhitelist.get(AliasKey)
    return None
