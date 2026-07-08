"""
autoanime 文本/标题/标点归一工具

对应原 `AutoAnimeMv.py`:
- `Auxiliary_HasChineseText`
- `Auxiliary_AsciiDoubleQuotesToCjk` / `Auxiliary_AsciiSingleQuotesToCjk`
- `Auxiliary_ConvertAsciiPunctuationToFullwidthCn`
- `Auxiliary_NormalizeDisplayTitle`
- `Auxiliary_NormalizeChinesePunctuation`
- `Auxiliary_NormalizeAliasKey`
- `Auxiliary_NormalizeApiTitle`
- `Auxiliary_ParseJsonFromAIContent`
"""

import json

from re import I, findall, match, search, sub

from zhconv.zhconv import convert


def Auxiliary_HasChineseText(TextValue):
    TextValue = '' if TextValue in [None, ''] else str(TextValue)
    return search(r'[\u4e00-\u9fff]', TextValue) != None


_SUBTITLE_GROUP_MARKERS = ('字幕組', '字幕组', '字幕', '汉化组', '漢化組', '發佈組', '发布组')


def Auxiliary_CleanFallbackTitle(RawName):
    '''清洗本地回退链路抽取出的 RAWName，尽量保留中文剧名本体。

    处理策略：
    1. 不含中文时原样返回，避免误伤纯英文标题。
    2. 去掉开头的字幕组/汉化组/发布组前缀（如 `六四位元字幕組=`）。
    3. 若包含 `/` 或全角 `／`，按分隔符分割后取第一个含中文的部分。
    4. 去掉尾部的非中文字符串（如 `=Otaku=ni=Yasashii=Gal=wa=Inai`）。
    '''
    RawName = '' if RawName in [None, ''] else str(RawName)
    if RawName == '':
        return ''
    if Auxiliary_HasChineseText(RawName) != True:
        return RawName

    # 统一分隔符
    T = RawName.replace('／', '/')
    # 按 / 分割取第一个含中文的段落
    if '/' in T:
        for part in T.split('/'):
            if Auxiliary_HasChineseText(part):
                T = part
                break

    # 按 = 分割，跳过字幕组前缀，然后取连续的中文段
    parts = [p for p in T.split('=') if p != '']
    result_parts = []
    skipped_group_prefix = False
    for part in parts:
        if not skipped_group_prefix:
            # 首段若包含字幕组标识，则视为前缀跳过
            if any(marker in part for marker in _SUBTITLE_GROUP_MARKERS):
                continue
            skipped_group_prefix = True
        if Auxiliary_HasChineseText(part):
            result_parts.append(part)
        else:
            # 一旦出现非中文段，后续都视为尾部冗余，停止
            break

    if result_parts == []:
        # 兜底：若什么都没留下，返回按等号合并后的原始串（已取第一段）
        return T.split('=')[0].strip() if '=' in T else T.strip()
    return ' '.join(result_parts)


def Auxiliary_AsciiDoubleQuotesToCjk(Title):
    if '"' not in Title:
        return Title
    Parts = Title.split('"')
    Out = [Parts[0]]
    for Idx in range(1, len(Parts)):
        Q = '\u201c' if (Idx % 2 == 1) else '\u201d'
        Out.append(Q + Parts[Idx])
    return ''.join(Out)


def Auxiliary_AsciiSingleQuotesToCjk(Title):
    if "'" not in Title:
        return Title
    Parts = Title.split("'")
    Out = [Parts[0]]
    for Idx in range(1, len(Parts)):
        Q = '\u2018' if (Idx % 2 == 1) else '\u2019'
        Out.append(Q + Parts[Idx])
    return ''.join(Out)


def Auxiliary_ConvertAsciiPunctuationToFullwidthCn(Title):
    '''含汉字的标题中将常见英文标点转为中文全角标点（英文纯拉丁标题不改动）。'''
    Title = '' if Title in [None, ''] else str(Title)
    if Title == '' or Auxiliary_HasChineseText(Title) != True:
        return Title
    TStrip = Title.strip()
    if match(r'^https?://', TStrip, I) != None:
        return Title
    T = Title
    T = sub(r'(?<=[\u4e00-\u9fff\u3000-\u303f\uff01-\uff60]):(?=[\u4e00-\u9fff])', '：', T)
    T = sub(r'(?<=[\u4e00-\u9fff]),(?=[\u4e00-\u9fff])', '，', T)
    T = sub(r'(?<=[\u4e00-\u9fff]),(\s+)(?=[\u4e00-\u9fff])', r'，\1', T)
    T = sub(r'(?<=[\u4e00-\u9fff]);(?=[\u4e00-\u9fff])', '；', T)
    T = sub(r'(?<=[\u4e00-\u9fff])!', '！', T)
    T = sub(r'!(?=[\u4e00-\u9fff])', '！', T)
    T = sub(r'(?<=[\u4e00-\u9fff])\?', '？', T)
    T = sub(r'\?(?=[\u4e00-\u9fff])', '？', T)
    T = sub(r'(?<=[\u4e00-\u9fff])/(?=[\u4e00-\u9fff])', '／', T)
    T = sub(r'(?<=[\u4e00-\u9fff])\(', '\uff08', T)
    T = sub(r'\((?=[\u4e00-\u9fff])', '\uff08', T)
    T = sub(r'(?<=[\u4e00-\u9fff])\)', '\uff09', T)
    T = sub(r'\)(?=[\u4e00-\u9fff])', '\uff09', T)
    T = Auxiliary_AsciiDoubleQuotesToCjk(T)
    T = Auxiliary_AsciiSingleQuotesToCjk(T)
    T = sub(r'(?<=[\u4e00-\u9fff])\.(?=\s*$)', '。', T)
    return T


def Auxiliary_NormalizeDisplayTitle(Title):
    Title = '' if Title in [None, ''] else str(Title)
    if Title == '':
        return ''
    Title = convert(Title, 'zh-hans')
    Title = Title.replace('\u3000', ' ')
    Title = Auxiliary_ConvertAsciiPunctuationToFullwidthCn(Title)
    Title = Title.strip().split('\n')[0].strip('`"\' ')
    Title = sub(r'\s+', ' ', Title).strip()
    Title = Title.replace('?', '？')
    return Title


def Auxiliary_NormalizeChinesePunctuation(Text):
    '''路径与展示名中文标点统一入口（移动/建目录前对路径分量调用）'''
    Text = '' if Text in [None, ''] else str(Text)
    if Text == '':
        return ''
    Text = convert(Text, 'zh-hans')
    Text = Text.replace('\u3000', ' ')
    Text = Auxiliary_ConvertAsciiPunctuationToFullwidthCn(Text)
    Text = sub(r'\s+', ' ', Text).strip()
    Text = Text.replace('?', '？')
    return Text


def Auxiliary_NormalizeAliasKey(Title):
    Title = Auxiliary_NormalizeDisplayTitle(Title).lower()
    if Title == '':
        return ''
    Title = sub(r'第\s*[0-9]{1,3}\s*季', '', Title, flags=I)
    Title = sub(r'[0-9]{1,3}(st|nd|rd|th)\s*season', '', Title, flags=I)
    Title = sub(r'season\s*[0-9]{1,3}', '', Title, flags=I)
    Title = sub(r'(^|[^a-z0-9])s\s*[0-9]{1,3}([^a-z0-9]|$)', ' ', Title, flags=I)
    Title = sub(r'[\[\]【】\(\)\uff08\uff09]', ' ', Title)
    Title = sub(r'[\-_/\\:：\.,，。!！?？~～]+', ' ', Title)
    Title = sub(r'\s+', '', Title)
    Title = sub(r'[^0-9a-z\u4e00-\u9fff]+', '', Title)
    return Title


def Auxiliary_NormalizeApiTitle(ApiTitle):
    ApiTitle = Auxiliary_NormalizeDisplayTitle(ApiTitle)
    if ApiTitle == '':
        return ''
    ApiTitle = sub(r'第.*?季|Season\s*[0-9]+|S[0-9]{1,2}$', '', ApiTitle, flags=I).strip('- []【】 ')
    return ApiTitle


def _RepairJsonText(Text):
    '''修复 LLM 返回的常见 JSON 语法错误（双引号、缺引号键名、尾部逗号等）。'''
    if not Text:
        return Text
    T = Text
    # 1. 修复重复引号 "" → "
    T = sub(r'""+', '"', T)
    # 2. 修复无引号键名：在 { 或 , 后紧跟字母/数字/_ 的键名，且后面跟着 : 和合法值开头
    #    例如 {season:1} → {"season":1} 或 ,anime_name_romaji":" → ,"anime_name_romaji":"
    for _ in range(5):
        NewT = sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([truefals0-9"\[\{])', r'\1"\2":\3', T)
        if NewT == T:
            break
        T = NewT
    # 3. 清理尾部多余逗号
    T = sub(r',\s*}', '}', T)
    T = sub(r',\s*\]', ']', T)
    return T


def Auxiliary_ParseJsonFromAIContent(Text):
    Text = '' if Text in [None, ''] else str(Text).strip()
    if Text == '':
        return None
    Text = sub(r'^```[a-zA-Z0-9_-]*\s*', '', Text)
    Text = sub(r'\s*```$', '', Text)
    try:
        return json.loads(Text)
    except Exception:
        pass
    # 方案3：容错清洗后再次尝试解析
    Cleaned = _RepairJsonText(Text)
    if Cleaned and Cleaned != Text:
        try:
            return json.loads(Cleaned)
        except Exception:
            pass
    if (X := findall(r'\{[\s\S]*\}', Text)) != []:
        try:
            return json.loads(X[0])
        except Exception:
            pass
        # 对提取出的 JSON 块也尝试清洗
        CleanedBlock = _RepairJsonText(X[0])
        if CleanedBlock != X[0]:
            try:
                return json.loads(CleanedBlock)
            except Exception:
                pass
    return None
