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
    if (X := findall(r'\{[\s\S]*\}', Text)) != []:
        try:
            return json.loads(X[0])
        except Exception:
            return None
    return None
