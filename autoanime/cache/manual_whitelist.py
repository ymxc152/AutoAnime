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
