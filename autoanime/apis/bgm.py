"""
autoanime BGM 查询模块

原 `AutoAnimeMv.py` 中 `USEBGMAPI` 开关与 `BgmAPIDataCache` 对应 bgm.tv 旧版接口。
当前实现与 `bangumi.py` 共用相同 bgm.tv 端点，因此 `Auxiliary_QueryBgmChineseTitle`
直接代理到 `Auxiliary_QueryBangumiChineseTitle`，但仅在 `state.USEBGMAPI == True`
时生效，方便 CLI 按开关独立控制。
"""

from .. import state
from .bangumi import Auxiliary_QueryBangumiChineseTitle


def Auxiliary_QueryBgmChineseTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''BGM 中文标题查询。当前与 Bangumi 使用同一 bgm.tv 端点，此函数受 USEBGMAPI 控制。'''
    if state.USEBGMAPI != True:
        return None
    return Auxiliary_QueryBangumiChineseTitle(QueryName, CandidateEn, CandidateRomaji, AliasList)
