"""
autoanime 命名/识别辅助（纯文本规则，不涉及网络）

对应原 `AutoAnimeMv.py`:
- `Auxiliary_SanitizePathComponent`
- `Auxiliary_FormatSEEPToken`
- `Auxiliary_UniformOTSTR`
- `Auxiliary_RMOTSTR`
- `Auxiliary_RMSubtitlingTeam`
- `Auxiliary_IDESE`
- `Auxiliary_IDEEP`
- `Auxiliary_IDEVDName`
- `Auxiliary_IDEASS`
- `Auxiliary_AnimeFileCheck`
- `Auxiliary_FileType`
- `Auxiliary_ASSFileCA`
- `Auxiliary_SubtitleLanguageSuffixForEmby`
- `Auxiliary_StripLeadingBracketReleaseTags`
"""

from os import path
from re import I, compile, findall, match, search, sub

from zhconv.zhconv import convert

from . import state
from .config_loader import Auxiliary_ParseInt
from .config_model import WINDOWS_RESERVED_NAMES
from .logging_utils import Auxiliary_Exit, Auxiliary_Log, Auxiliary_FormatListPreview
from .text_utils import Auxiliary_HasChineseText, Auxiliary_NormalizeDisplayTitle


def Auxiliary_SanitizePathComponent(Name, MaxLen=None):
    '''清洗文件名/目录名，避免 Windows 非法字符与保留名'''
    if Name in [None, '']:
        Name = 'Unknown'
    Name = Auxiliary_NormalizeDisplayTitle(Name).replace('\n', ' ').replace('\r', ' ')
    # 拒绝明显不是标题的文本（解释性回复、空字符串标记等）
    _REJECT_PATTERNS = ('空字符串', '无法对应', '并非已知', '根据指令', '注经查询', '请提供', '请输入')
    for _rp in _REJECT_PATTERNS:
        if _rp in Name:
            Auxiliary_Log(f'SanitizePathComponent: 检测到非法模式「{_rp}」，回退为 Unknown: {Name[:60]}', 'WARNING')
            Name = 'Unknown'
            break
    if len(Name) > 80 and Auxiliary_HasChineseText(Name):
        # 中文标题超过 80 字符大概率是 AI 解释性回复
        Auxiliary_Log(f'SanitizePathComponent: 中文名过长({len(Name)}字符)，疑似解释性文本，回退为 Unknown: {Name[:60]}…', 'WARNING')
        Name = 'Unknown'
    Name = sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Name)
    # 注意：全角 ？！。， 等在 Windows 文件名中是合法的，保留
    Name = sub(r'\s+', ' ', Name).strip(' .')
    if Name == '' or Name in ('_', '__', '___'):
        Name = 'Unknown'
    if Name.upper() in WINDOWS_RESERVED_NAMES:
        Name = f'{Name}_'
    Limit = Auxiliary_ParseInt(MaxLen, 180) if MaxLen not in [None, ''] else 180
    if Limit < 16:
        Limit = 16
    if len(Name) > Limit:
        Name = Name[:Limit].rstrip(' .')
    Name = sub(r'[\s_\-–—]+$', '', Name).strip(' .')
    return Name if Name != '' else 'Unknown'


def Auxiliary_FormatSEEPToken(Token):
    Token = str(Token)
    if Token.isdigit():
        return Token.zfill(2)
    return Token


def Auxiliary_UniformOTSTR(File):
    '''统一意外字符'''

    NewFile = convert(File, 'zh-hans')
    NewUSTRFile = sub(r',|，| ', '-', NewFile, flags=I)
    # 保留 ~ 字符（包括全角和半角），不替换成 =
    NewUSTRFile = sub(r'[^a-z0-9\s&/:：.\-\(\)（）《》\u4e00-\u9fa5\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF°ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ~～!！]', '=', NewUSTRFile, flags=I)
    # 异种剧集统一
    OtEpisodesMatchData = [r'第(\d{1,4})集', r'(\d{1,4})集', r'第(\d{1,4})话', r'(\d{1,4})END', r'(\d{1,4}) END', r'(\d{1,4})E']
    for i in OtEpisodesMatchData:
        i = f'[^0-9a-z]{i}[^0-9a-z]'
        if search(i, NewUSTRFile, flags=I) != None:
            a = search(i, NewUSTRFile, flags=I)
            NewUSTRFile = NewUSTRFile.replace(a.group(), '=' + a.group(1).strip('\u4e00-\u9fa5') + '=')
    return NewUSTRFile


def Auxiliary_RMOTSTR(File):
    '''剔除意外字符'''

    NewPSTRFile = File
    FuzzyMatchData = [r'(.*?|=)月新番(.*?|=)', r'\d{4}.\d{2}.\d{2}', r'20\d{2}', r'v[2-9]', r'\d{4}年\d{1,2}月番']
    PreciseMatchData = [r'仅限港澳台地区', r'年龄限制版', r'国漫', r'x264', r'1080p', r'720p', r'4k', r'（-）']
    # 同步到 state，避免测试/扩展访问旧属性时报错
    state.FuzzyMatchData = FuzzyMatchData
    state.PreciseMatchData = PreciseMatchData
    for i in PreciseMatchData:
        NewPSTRFile = sub(r'%s' % i, '=', NewPSTRFile, flags=I)
    for i in FuzzyMatchData:
        NewPSTRFile = sub(i, '=', NewPSTRFile, flags=I)
    return NewPSTRFile


def Auxiliary_RMSubtitlingTeam(File):
    '''剔除字幕组信息'''

    if File[0] == '《':
        File = sub(r'《|》', '', File, flags=I)
    else:
        File = sub(r'^=.*?=', '', File, flags=I)
    return File


_STRIP_LEADING_BRACKET_RELEASE_TAGS = compile(r'^(?:(?:\s*\[[^\]]+\]|\s*【[^】]+】))+\s*')


def Auxiliary_StripLeadingBracketReleaseTags(basename):
    """仅用于展示/LLM 输入：去掉基名首部的连续半角 […] 或全角 【…】 发行/字幕组标签。剥空则回退为原串。"""

    if basename is None:
        return None
    if basename == '':
        return ''
    s = str(basename)
    t = _STRIP_LEADING_BRACKET_RELEASE_TAGS.sub('', s, count=1)
    t = t.lstrip() if t else t
    if t == '':
        return s
    return t


def Auxiliary_IDESE(File):
    '''识别剧季并截断Name'''

    SeasonMatchData = r'(季(.*?)第)|(([0-9]{0,1}[0-9]{1})S)|(([0-9]{0,1}[0-9]{1})nosaeS)|(([0-9]{0,1}[0-9]{1}) nosaeS)|(([0-9]{0,1}[0-9]{1})-nosaeS)|(nosaeS-dn([0-9]{1}))|(nosaeS-dr([0-9]{1}))'
    if (X := findall(SeasonMatchData, File[::-1], flags=I)) != []:
        SEData = X
        SENamelist = []
        SEList = []
        for sedata in SEData:
            for se in sedata:
                if se != '' and se.isnumeric() == False:
                    SENamelist.append(se[::-1])
                elif se.isnumeric() == True:
                    SEList.append(se)
        for i in SENamelist:
            File = sub(r'%s.*' % i, '', File, flags=I).strip('=')
        for i in range(len(SEList)):
            if SEList[i].isdecimal() == True:
                SE = SEList[i][::-1]
            elif '\u0e00' <= SEList[i] <= '\u9fa5':
                digit = {'一': '01', '二': '02', '三': '03', '四': '04', '五': '05', '六': '06', '七': '07', '八': '08', '九': '09',
                         '壹': '01', '贰': '02', '叁': '03', '肆': '04', '伍': '05', '陆': '06', '柒': '07', '捌': '08', '玖': '09'}
                SE = digit[SEList[i]]
            if SE is not None:
                return SE, File, SENamelist[0]
    elif (X := findall(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]', File[::-1], flags=I)) != []:
        A = {'Ⅰ': '01', 'Ⅱ': '02', 'Ⅲ': '03', 'Ⅳ': '04', 'Ⅴ': '05', 'Ⅵ': '06', 'Ⅶ': '07', 'Ⅷ': '08', 'Ⅸ': '09', 'Ⅹ': '10', 'Ⅺ': '11', 'Ⅻ': '12'}
        return A[X[0]], File, X[0]
    else:
        return '01', File, ''


def Auxiliary_IDEEP(File, *, _quiet_subtitle: bool = False):
    '''识别剧集。`_quiet_subtitle=True` 时不在失败分支打日志，供 `Auxiliary_IDEASS` 末尾批量汇总。'''

    # 优先识别国际通用 SxxExx / Exx 剧集标记。
    # 原反向正则使用 flags=I，会把大写 E/S 也当作字母排除，导致 S02E01 这类格式无法抽出集号；
    # 先正向匹配可绕过该问题，同时避免 H.264、AAC2.0 等技术参数被误识别为剧集。
    ExplicitMatch = search(r'[Ss](\d{1,2})[Ee](\d{1,4})(?!\d)', File)
    if ExplicitMatch is not None:
        return ExplicitMatch.group(2)
    ExplicitMatch = search(r'(?<![a-z0-9])[Ee](\d{1,4})(?!\d)', File)
    if ExplicitMatch is not None:
        return ExplicitMatch.group(1)

    try:
        if findall(r'[^0-9.\u4e00-\u9fa5\u0800-\u4e00]([0-9.]{1,4}-[0-9.]{1,4})[^0-9.\u4e00-\u9fa5\u0800-\u4e00]', File[::-1], flags=I) != []:
            if _quiet_subtitle != True:
                Auxiliary_Log('剧集包不予处理', 'WARNING')
            raise Exception()
        elif (X := findall(r'[^0-9a-z.\u4e00-\u9fa5\u0800-\u4e00]([0-9.]{1,5})[^0-9a-uw-z.\u4e00-\u9fa5\u0800-\u4e00]', File[::-1], flags=I)) != []:
            Episodes = X[0][::-1].strip(" =-_eEv")
        else:
            Episodes = findall(r'[^0-9a-z.\u4e00-\u9fa5\u0800-\u4e00]([0-9]{1,4})[^0-9a-uw-z.\u4e00-\u9fa5\u0800-\u4e00]', File[::-1], flags=I)[0][::-1].strip(" =-_eEv")
    except IndexError:
        if _quiet_subtitle != True:
            Auxiliary_Log('未匹配出剧集,请检查(程序目前不支持电影动漫)', 'WARNING')
        raise Exception()
    except Exception:
        raise Exception()
    else:
        return Episodes


def Auxiliary_IDEVDName(File, RAWEP):
    '''识别剧名'''

    try:
        # 优先处理 SxxExx 格式：把季集标记整体作为截断点，避免只按反向集号截断留下 S02E 等残留。
        SeasonEpisodeMatch = search(r'[Ss]\d{1,2}[Ee]\d{1,4}', File)
        if SeasonEpisodeMatch is not None:
            VDName = File[:SeasonEpisodeMatch.start()].strip('=.-_ ')
            if VDName:
                Auxiliary_Log(f'通过剧集截断文件名 ==> {VDName}', 'INFO')
                return VDName

        match_result = search(r'[=|-]%s[=|-](.*)' % RAWEP[::-1], File[::-1], flags=I)
        if match_result:
            VDName = match_result.group(1).strip('=-=-=-')[::-1]
        else:
            VDName = sub(r'.*%s' % RAWEP[::-1], '', File[::-1], count=0, flags=I).strip('=-=-=-')[::-1]
            if not VDName or VDName == File:
                VDName = path.splitext(File)[0]
        Auxiliary_Log(f'通过剧集截断文件名 ==> {VDName}', 'INFO')
        return VDName
    except Exception as e:
        Auxiliary_Log(f'剧名识别失败，使用原始文件名: {e}', 'WARNING')
        return path.splitext(File)[0]


def Auxiliary_IDEASS(File, SE, EP, ASSList):
    '''识别当前番剧视频的所属字幕文件'''

    ASSFileList = []
    SkippedNoEp = []
    for ASSFile in ASSList:
        ASSName = Auxiliary_UniformOTSTR(path.basename(ASSFile))
        try:
            ASSEP = Auxiliary_IDEEP(ASSName, _quiet_subtitle=True)
        except Exception:
            SkippedNoEp.append(ASSFile)
            continue
        if File in ASSName and EP == ASSEP and SE in ASSName:
            ASSFileList.append(ASSFile)
    if SkippedNoEp:
        Auxiliary_Log(
            f'字幕文件无法提取剧集，跳过 {len(SkippedNoEp)} 个: {Auxiliary_FormatListPreview(SkippedNoEp)}',
            'WARNING',
        )
    ASSFileList = None if ASSFileList == [] else ASSFileList
    return ASSFileList


def Auxiliary_FileType(FileName):
    '''识别文件类型'''

    SuffixList = {'.ass': 'ASS', '.srt': 'ASS', '.mp4': 'MP4', '.mkv': 'MP4', '.log': 'LOG'}
    for FileType in SuffixList:
        if match(FileType[::-1], FileName[::-1], flags=I) != None:
            try:
                return SuffixList[FileType.lower()]
            except Exception:
                Auxiliary_Exit('文件类型不正确')


def Auxiliary_AnimeFileCheck(File):
    '''检查是否为 OP/CM/SP/PV 等非正片'''

    Checklist = ['OP', 'CM', 'SP', 'PV']
    for i in Checklist:
        if search(f'[-=]{i}[-=]', File, flags=I) != None:
            return i
    return True


def Auxiliary_ASSFileCA(ASSFileName):
    '''字幕文件的语言分类'''

    ASSFileName = path.basename(ASSFileName)
    SubtitleList = [['简', '簡', '簡體', 'sc', 'chs', 'GB'], ['繁', 'tc', 'cht', 'BIG5'], ['日', 'jp']]
    for i in range(len(SubtitleList)):
        for ii in SubtitleList[i]:
            if search(f'[^0-9a-z]{ii[::-1]}[^0-9a-z]', ASSFileName[::-1], flags=I) != None:
                if i == 0:
                    return '.chs' if state.JELLYFINFORMAT == False else '.简体中文.chi'
                elif i == 1:
                    return '.cht' if state.JELLYFINFORMAT == False else '.繁体中文.chi'
                elif i == 2:
                    return '.jp'
    return '.other'


def Auxiliary_SubtitleLanguageSuffixForEmby(ASSFileName):
    RawSuffix = Auxiliary_ASSFileCA(ASSFileName)
    Mapping = {
        '.chs': '.zh-CN',
        '.cht': '.zh-TW',
        '.jp': '.ja',
        '.other': '.und',
    }
    return Mapping.get(RawSuffix, '.und')
