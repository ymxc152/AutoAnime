"""
autoanime ShowOrganizationIndex（已整理集标记）

对应原 `AutoAnimeMv.py`:
- `Auxiliary_FormatOrganizedEpisodeTag`
- `Auxiliary_GetShowOrganizationRecord`
- `Auxiliary_OrderedShowRecordDict`
- `Auxiliary_SetShowOrganizationRecord`
- `Auxiliary_ShowHasOrganizedEpisode`
- `Auxiliary_ShowMarkOrganizedEpisode`

增量能力（fix_show_index todo）：
- `Auxiliary_ShowHasOrganizedEpisode` 升级为 `(has_tag, expected_dst)`，由 pipeline 再做
  同物理文件判定；
- `Auxiliary_ShowClearOrganizedEpisode` 提供 tag 自愈剔除；
- `Auxiliary_ShowGetEpisodeExpectedDst` / `Auxiliary_ShowSetEpisodeExpectedDst` 负责 `episode_last_dst`
  字段的读写，后者在 `ShowMarkOrganizedEpisode` 成功落盘时由 pipeline 调用，记录上次目标路径。
"""

from pathlib import Path as PathlibPath
from time import localtime, strftime, time

from .. import state
from ..naming import Auxiliary_FormatSEEPToken
from ..text_utils import Auxiliary_NormalizeApiTitle, Auxiliary_NormalizeDisplayTitle
from .persistent import Auxiliary_GetPersistentCache, Auxiliary_SetPersistentCache


def Auxiliary_FormatOrganizedEpisodeTag(SE, EP):
    SEValue = Auxiliary_FormatSEEPToken(SE)
    EPValue = Auxiliary_FormatSEEPToken(EP)
    return f'S{SEValue}E{EPValue}'


def Auxiliary_GetShowOrganizationRecord(CanonicalID):
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return None
    if CanonicalID in state.ShowOrganizationIndexDataCache:
        return state.ShowOrganizationIndexDataCache[CanonicalID]
    Raw = Auxiliary_GetPersistentCache('ShowOrganizationIndex', CanonicalID)
    if type(Raw) != dict:
        return None
    state.ShowOrganizationIndexDataCache[CanonicalID] = Raw
    return Raw


def Auxiliary_OrderedShowRecordDict(Record):
    if type(Record) != dict:
        Record = {}
    EpisodeLastDst = Record.get('episode_last_dst', {})
    if type(EpisodeLastDst) != dict:
        EpisodeLastDst = {}
    # 清洗：只保留 str->str 的 tag->path 映射
    CleanedLastDst = {}
    for K, V in EpisodeLastDst.items():
        if K in [None, ''] or V in [None, '']:
            continue
        CleanedLastDst[str(K)] = str(V)
    v = int(Record.get('v', 1))
    if v < 2:
        v = 2
    return {
        'canonical_id': str(Record.get('canonical_id', '')),
        'organized_episodes': list(Record.get('organized_episodes', [])) if type(Record.get('organized_episodes')) == list else [],
        'episode_last_dst': CleanedLastDst,
        'title_en': str(Record.get('title_en', '')),
        'title_romaji': str(Record.get('title_romaji', '')),
        'title_zh': str(Record.get('title_zh', '')),
        'first_organized_at': str(Record.get('first_organized_at', '')),
        'last_organized_at': str(Record.get('last_organized_at', '')),
        'v': v,
    }


def Auxiliary_SetShowOrganizationRecord(CanonicalID, Record):
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return
    now_ts = strftime("%Y-%m-%d %H:%M:%S", localtime(time()))
    Record = Record.copy()
    Record['canonical_id'] = CanonicalID
    if 'organized_episodes' not in Record or type(Record['organized_episodes']) != list:
        Record['organized_episodes'] = []
    if str(Record.get('first_organized_at', '')).strip() in [None, '']:
        Record['first_organized_at'] = now_ts
    Record['last_organized_at'] = now_ts
    Record['v'] = 2
    Ordered = Auxiliary_OrderedShowRecordDict(Record)
    state.ShowOrganizationIndexDataCache[CanonicalID] = Ordered
    Auxiliary_SetPersistentCache('ShowOrganizationIndex', CanonicalID, Ordered)


def Auxiliary_ShowGetEpisodeExpectedDst(CanonicalID, SE, EP):
    '''返回上一次此 (Canonical, SE, EP) 成功落盘的目标绝对路径字符串；无记录时返回空串。'''
    Rec = Auxiliary_GetShowOrganizationRecord(CanonicalID)
    if type(Rec) != dict:
        return ''
    LastDstMap = Rec.get('episode_last_dst', {})
    if type(LastDstMap) != dict:
        return ''
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    V = LastDstMap.get(Tag, '')
    return str(V) if V not in [None, ''] else ''


def Auxiliary_ShowHasOrganizedEpisode(CanonicalID, SE, EP):
    '''校验 (CanonicalID, SE, EP) 是否已整理。

    返回 `(has_tag: bool, expected_dst: Path|None)`：
    - `has_tag` : ShowOrganizationIndex 中是否打过整理标签；
    - `expected_dst` : 若记录过 `episode_last_dst`，返回 pathlib.Path；否则返回 None。

    向后兼容：旧调用 `if Auxiliary_ShowHasOrganizedEpisode(...)` 依赖 bool 判定；
    tuple 在 `bool(...)` 下始终为 True，但现有新调用方应当使用 `has_tag, _ = ...` 解构。
    为避免旧布尔语义误伤，若仅有 tag 而无 expected_dst，`__bool__` 仍保持 True，
    历史调用仅落在本包内（migrate 已迁移），外部不会直接调用。
    '''
    Rec = Auxiliary_GetShowOrganizationRecord(CanonicalID)
    if type(Rec) != dict:
        return False, None
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    EpList = Rec.get('organized_episodes', [])
    if type(EpList) != list or Tag not in EpList:
        return False, None
    ExpectedDstStr = Auxiliary_ShowGetEpisodeExpectedDst(CanonicalID, SE, EP)
    if ExpectedDstStr in [None, '']:
        return True, None
    try:
        return True, PathlibPath(ExpectedDstStr)
    except Exception:
        return True, None


def Auxiliary_ShowClearOrganizedEpisode(CanonicalID, SE, EP):
    '''自愈剔除：当目标文件缺失时，从 `organized_episodes` 与 `episode_last_dst` 中摘掉此集 tag。

    返回 True 表示确实做了修改，便于上层打一条 INFO 日志。
    '''
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return False
    Rec = Auxiliary_GetShowOrganizationRecord(CanonicalID)
    if type(Rec) != dict:
        return False
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    EpList = list(Rec.get('organized_episodes', [])) if type(Rec.get('organized_episodes')) == list else []
    LastDstMap = dict(Rec.get('episode_last_dst', {})) if type(Rec.get('episode_last_dst')) == dict else {}
    Changed = False
    if Tag in EpList:
        EpList = [E for E in EpList if E != Tag]
        Changed = True
    if Tag in LastDstMap:
        LastDstMap.pop(Tag, None)
        Changed = True
    if Changed == False:
        return False
    Rec['organized_episodes'] = sorted(EpList)
    Rec['episode_last_dst'] = LastDstMap
    Auxiliary_SetShowOrganizationRecord(CanonicalID, Rec)
    return True


def Auxiliary_ShowSetEpisodeExpectedDst(CanonicalID, SE, EP, DstPath):
    '''写入 `episode_last_dst[tag] = DstPath`，用于下次判定目标文件是否仍然存在。'''
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '' or DstPath in [None, '']:
        return
    Rec = Auxiliary_GetShowOrganizationRecord(CanonicalID)
    if type(Rec) != dict:
        return
    LastDstMap = dict(Rec.get('episode_last_dst', {})) if type(Rec.get('episode_last_dst')) == dict else {}
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    LastDstMap[Tag] = str(DstPath)
    Rec['episode_last_dst'] = LastDstMap
    Auxiliary_SetShowOrganizationRecord(CanonicalID, Rec)


def Auxiliary_ShowFindCrossCanonicalEpisode(SE, EP):
    '''遍历所有 ShowOrganizationIndex 记录，查找是否有任何 CanonicalID 已记录同 SE/EP 且 expected_dst 指向的目标文件仍存在。
    返回 (CanonicalID, ExpectedDstPath) 或 (None, None)。
    '''
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    for CID, Rec in state.ShowOrganizationIndexDataCache.items():
        if type(Rec) != dict:
            continue
        EpList = Rec.get('organized_episodes', [])
        if type(EpList) != list or Tag not in EpList:
            continue
        LastDstMap = Rec.get('episode_last_dst', {})
        if type(LastDstMap) != dict:
            continue
        DstStr = LastDstMap.get(Tag, '')
        if DstStr in [None, '']:
            continue
        try:
            DstPath = PathlibPath(DstStr)
            if DstPath.exists():
                return CID, DstPath
        except Exception:
            continue
    return None, None


def Auxiliary_ShowClearDuplicateDstPath(DstPath, ExcludeCanonicalID=None, ExcludeTag=None):
    '''清理所有 CanonicalID 的 episode_last_dst 中指向同一 DstPath 的旧记录（可排除指定组合）。'''
    if DstPath in [None, '']:
        return False
    TargetStr = str(DstPath)
    ChangedAny = False
    for CID, Rec in list(state.ShowOrganizationIndexDataCache.items()):
        if type(Rec) != dict:
            continue
        LastDstMap = dict(Rec.get('episode_last_dst', {})) if type(Rec.get('episode_last_dst')) == dict else {}
        TagsToRemove = []
        for Tag, PathStr in list(LastDstMap.items()):
            if PathStr == TargetStr:
                if ExcludeCanonicalID and CID == ExcludeCanonicalID and ExcludeTag and Tag == ExcludeTag:
                    continue
                LastDstMap.pop(Tag, None)
                TagsToRemove.append(Tag)
                ChangedAny = True
        if TagsToRemove:
            EpList = list(Rec.get('organized_episodes', [])) if type(Rec.get('organized_episodes')) == list else []
            EpList = [E for E in EpList if E not in TagsToRemove]
            Rec['organized_episodes'] = sorted(EpList)
            Rec['episode_last_dst'] = LastDstMap
            Auxiliary_SetShowOrganizationRecord(CID, Rec)
    return ChangedAny


def Auxiliary_ShowMarkOrganizedEpisode(CanonicalID, title_zh, title_en, title_romaji, SE, EP, DstPath=None):
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return
    Rec = Auxiliary_GetShowOrganizationRecord(CanonicalID)
    if type(Rec) != dict:
        Rec = {
            'canonical_id': CanonicalID,
            'title_zh': '',
            'title_en': '',
            'title_romaji': '',
            'organized_episodes': [],
            'episode_last_dst': {},
            'v': 2,
            'first_organized_at': '',
            'last_organized_at': '',
        }
    EpList = list(Rec.get('organized_episodes', [])) if type(Rec.get('organized_episodes')) == list else []
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    if Tag not in EpList:
        EpList.append(Tag)
    Rec['organized_episodes'] = sorted(EpList)
    Rec['title_zh'] = Auxiliary_NormalizeApiTitle(title_zh or Rec.get('title_zh', ''))
    Rec['title_en'] = Auxiliary_NormalizeDisplayTitle(title_en or Rec.get('title_en', ''))
    Rec['title_romaji'] = Auxiliary_NormalizeDisplayTitle(title_romaji or Rec.get('title_romaji', ''))
    if DstPath not in [None, '']:
        LastDstMap = dict(Rec.get('episode_last_dst', {})) if type(Rec.get('episode_last_dst')) == dict else {}
        LastDstMap[Tag] = str(DstPath)
        Rec['episode_last_dst'] = LastDstMap
    Auxiliary_SetShowOrganizationRecord(CanonicalID, Rec)
