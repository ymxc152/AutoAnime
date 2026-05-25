"""
autoanime 日志工具

对应原 `AutoAnimeMv.py` 中下列函数：
- `Auxiliary_Log`
- `Auxiliary_ShouldPrintConsoleLog`
- `Auxiliary_WriteLog`
- `Auxiliary_Exit`
- `Auxiliary_FormatListPreview`
- `Auxiliary_DeleteLogs`

所有函数名与原单文件保持一致，方便直接 `from autoanime.logging_utils import Auxiliary_Log`。
"""

from datetime import datetime
from os import makedirs, path, remove
from re import I, match
from time import localtime, strftime, time

from . import state


def Auxiliary_ShouldPrintConsoleLog(OneMsg, MsgFlag='INFO', flag=None):
    '''控制终端输出，只保留与番剧整理直接相关的信息'''

    if flag == 'PRINT':
        return True
    if MsgFlag != 'INFO':
        return True
    OneMsg = str(OneMsg).strip()
    if OneMsg == '':
        return False
    if set(OneMsg) == {'-'}:
        return False

    SilentPrefixes = (
        '正在读取外置ini文件',
        '读取到配置分区:',
        '配置 < ',
        '当前工具版本为',
        '当前操作系统识别码为',
        'filepath < ',
        'filename < ',
        'number < ',
        'categoryname < ',
        'animename < ',
        'tag < ',
        'NAMING_STYLE < ',
        'DRY_RUN < ',
        'OUTPUT_PATH < ',
        'STRICT_MODE < ',
        'USELINK < ',
        '当前分类 >> ',
        '排除模块：',
        '模块 << ',
        '无扩展',
        '不存在扩展文件夹 ./Ext',
        '已加载持久化缓存文件 ',
        '持久化缓存写入完成 ',
        'OpenAI文件识别缓存标题已按标准化缓存修正:',
        'OpenAI文件识别缓存命中 << ',
        'OpenAI文件识别持久化缓存标题已按标准化缓存修正:',
        'OpenAI文件识别持久化缓存命中 << ',
        '没有使用OpenAIApi进行检索',
        '没有使用BgmApi进行检索',
        '没有使用TMDBApi进行检索',
        '没有使用BangumiApi进行检索',
        '代理功能开启',
        '使用系统代理',
    )
    SilentSubstrings = (
        '秒延时中',
        '个可加载模块',
        '内存缓存查询结果',
        '持久化缓存查询结果',
        'OpenAIApi查询结果',
        'BgmApi查询结果',
        'TMDBApi查询结果',
        'BangumiApi查询结果',
        'API获取到结果',
    )
    if any(OneMsg.startswith(Prefix) for Prefix in SilentPrefixes):
        return False
    if any(Keyword in OneMsg for Keyword in SilentSubstrings):
        return False
    return True


def Auxiliary_Log(Msg: str, MsgFlag='INFO', flag=None, end='\n'):
    '''日志'''

    Msg = Msg if type(Msg) == tuple else (Msg,)
    for OneMsg in Msg:
        FormattedMsg = f'[{strftime("%Y-%m-%d %H:%M:%S", localtime(time()))}] {MsgFlag}: {OneMsg}'
        if (state.PRINTLOGFLAG == True or flag == 'PRINT') and Auxiliary_ShouldPrintConsoleLog(OneMsg, MsgFlag, flag):
            print(FormattedMsg, end=end)
        state.LogData = state.LogData + '\n' + FormattedMsg if state.LogData not in [None, ''] else FormattedMsg


def Auxiliary_FormatListPreview(FileList, preview_count=12):
    '''列表日志预览，避免一次性输出过长内容拖慢终端'''

    if type(FileList) != list:
        return str(FileList)
    TotalCount = len(FileList)
    if TotalCount <= preview_count:
        return str(FileList)
    PreviewList = FileList[:preview_count]
    return f'{PreviewList} ... 省略{TotalCount - preview_count}项'


def Auxiliary_DeleteLogs():
    '''日志清理'''

    RmLogsList = []
    if state.RMLOGSFLAG != False and state.LogsFileList != []:
        ToDay = datetime.strptime(datetime.now().strftime('%Y-%m-%d'), "%Y-%m-%d").date()
        for Logs in state.LogsFileList:
            LogFileName = path.basename(Logs)
            if match(r'^\d{4}-\d{2}-\d{2}\.log$', LogFileName, flags=I) == None:
                continue
            LogDate = datetime.strptime(LogFileName.replace('.log', ''), "%Y-%m-%d").date()
            if (ToDay - LogDate).days >= int(state.RMLOGSFLAG):
                remove(f'{state.Path}{state.Separator}{Logs}')
                RmLogsList.append(Logs)
        if RmLogsList != []:
            Auxiliary_Log(f'清理了保存时间达到和超过{state.RMLOGSFLAG}天的日志文件 << {RmLogsList}')


def Auxiliary_WriteLog():
    '''写log文件'''

    LogPath = state.filepath if state.filepath not in [None, ''] and path.exists(state.filepath) == True else state.PyPath
    if LogPath in [None, '']:
        from pathlib import Path as _P
        LogPath = str(_P('.').resolve())
    if path.exists(LogPath) == False:
        makedirs(LogPath, exist_ok=True)
    if LogPath == state.PyPath:
        Out = str(getattr(state, 'OUTPUT_PATH', '') or '').strip()
        if Out == '':
            Auxiliary_Log('Log文件保存在工具目录下', 'WARNING')
    with open(f'{LogPath}{state.Separator}{strftime("%Y-%m-%d", localtime(time()))}.log', 'a+', encoding='UTF-8') as LogFile:
        LogFile.write(state.LogData)


def Auxiliary_Exit(LogMsg):
    '''因可预见错误离场'''

    Auxiliary_Log(LogMsg, 'EXIT', flag='PRINT')
    raise SystemExit(0)
