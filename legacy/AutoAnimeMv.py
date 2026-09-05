#!/usr/bin/python3
#coding:utf-8
"""
AutoAnimeMv - 番剧文件自动整理工具
Copyright (C) 2024 AutoAnimeMv Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
import argparse
import json
from dataclasses import dataclass,field
from pathlib import Path as PathlibPath
from sys import argv,executable #获取外部传参和外置配置更新
from os import environ,path,name,makedirs,listdir,link,remove,removedirs,renames # os操作
from time import sleep,strftime,localtime,time # 时间相关
from datetime import datetime # 时间相减用
from re import compile,findall,match,search,sub,I # 匹配相关
from shutil import move # 移动File
from ast import literal_eval # srt转化
from typing import Optional
from zhconv.zhconv import convert # 繁化简
import zhconv.zhconv as zhconv_module
from urllib.parse import quote,unquote # url encode
from requests import get,post,exceptions # 网络部分
from urllib.request import getproxies  # 获取系统代理
#from random import randint # 随机数生成
#from threading import Thread # 多线程
from importlib import import_module # 动态加载模块


WINDOWS_RESERVED_NAMES = {
    'CON','PRN','AUX','NUL',
    'COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9',
    'LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9'
}

TMDBTvSeasonLayoutMemoryCache = {}
TMDBTvSeriesIdMemoryCache = {}

class _OpenAISkipSpIdentification:
    '''OpenAI 将文件识别为 SP 特典时的哨兵，主流程跳过整理'''
    pass

OPENAI_SKIP_SP_IDENTIFICATION = _OpenAISkipSpIdentification()

LastOpenAIIdentifyFailure = None


@dataclass
class Config:
    naming_style: str = 'default'
    dry_run: bool = False
    cache_dir: str = '.cache'
    cache_ttl_seconds: int = 86400
    tmdb_token_env: str = 'TMDB_BEARER_TOKEN'
    openai_key_env: str = 'OPENAI_API_KEY'
    openai_identify_all: bool = True
    strict_mode: bool = True
    output_path: str = ''


@dataclass
class RuntimeContext:
    source_path: PathlibPath = PathlibPath('.')
    output_path: PathlibPath = PathlibPath('.')
    category_name: str = ''
    config: Config = field(default_factory=Config)
    operation_log_path: Optional[PathlibPath] = None
    rollback_log_path: Optional[PathlibPath] = None
    operation_records: list = field(default_factory=list)
#Start 开始部分进行程序的初始化 


def Auxiliary_InitZhconvDictionarySafely():
    '''安全预加载 zhconv 词典，避免第三方资源句柄泄漏警告'''
    try:
        if getattr(zhconv_module, 'zhcdicts', None) is not None:
            return
        DictFile = getattr(zhconv_module, 'DICTIONARY', 'zhcdict.json')
        DefaultDictFile = getattr(zhconv_module, '_DEFAULT_DICT', 'zhcdict.json')
        RawBytes = b''
        ResourceStream = None
        if DictFile == DefaultDictFile and hasattr(zhconv_module, 'get_module_res'):
            ResourceStream = zhconv_module.get_module_res(DictFile)
            if ResourceStream not in [None, '']:
                try:
                    RawBytes = ResourceStream.read()
                finally:
                    if hasattr(ResourceStream, 'close'):
                        try:
                            ResourceStream.close()
                        except Exception:
                            pass
        else:
            with open(DictFile, 'rb') as f:
                RawBytes = f.read()
        if RawBytes in [None, b'']:
            return
        DictData = json.loads(RawBytes.decode('utf-8'))
        DictData['SIMPONLY'] = frozenset(DictData.get('SIMPONLY', []))
        DictData['TRADONLY'] = frozenset(DictData.get('TRADONLY', []))
        zhconv_module.zhcdicts = DictData
    except Exception:
        # 回退到 zhconv 默认懒加载逻辑，不中断主流程
        return

def Start_PATH(**kwargs) -> dict:
    '''初始化'''
    # 版本 数据库缓存 Api数据缓存 Log数据集 分隔符
    global Versions,AimeListCache,BgmAPIDataCache,TMDBAPIDataCache,BangumiAPIDataCache,OpenAIAPIDataCache,OpenAIIdentifyFileMemoryCache,ShowOrganizationIndexDataCache,TitleAliasIndexDataCache,CanonicalTitleIndexDataCache,EpisodeDecisionDataCache,LastOpenAIFileInfoMeta,LogData,Separator,Proxy,TgBotMsgData,PyPath,Runtime,PersistentApiCache,PersistentApiCacheDirty,CurrentRunID,LastIdentificationFromAI,LastOpenAIIdentifyFailure,ManualTitleWhitelistDataCache,ManualTitleWhitelistMTime,TMDBTvSeasonLayoutMemoryCache,TMDBTvSeriesIdMemoryCache
    Versions = '3.(4.5).6'
    AimeListCache = None
    BgmAPIDataCache = {}
    TMDBAPIDataCache = {}
    BangumiAPIDataCache = {}
    OpenAIAPIDataCache = {}
    OpenAIIdentifyFileMemoryCache = {}
    ShowOrganizationIndexDataCache = {}
    TitleAliasIndexDataCache = {}
    CanonicalTitleIndexDataCache = {}
    EpisodeDecisionDataCache = {}
    LastOpenAIFileInfoMeta = {}
    LastIdentificationFromAI = False
    LastOpenAIIdentifyFailure = None
    PersistentApiCache = {}
    PersistentApiCacheDirty = False
    ManualTitleWhitelistDataCache = {}
    ManualTitleWhitelistMTime = 0.0
    TMDBTvSeasonLayoutMemoryCache = {}
    TMDBTvSeriesIdMemoryCache = {}
    LogData = f'\n\n[{strftime("%Y-%m-%d %H:%M:%S",localtime(time()))}] INFO: Running....'
    Separator = '\\' if name == 'nt' else '/'
    TgBotMsgData = ''
    PyPath = str(PathlibPath(__file__).resolve().parent)
    CurrentRunID = strftime('%Y%m%d_%H%M%S',localtime(time()))
    Runtime = RuntimeContext()
    Auxiliary_InitZhconvDictionarySafely()

    global USEMODULE,USEPROXY,USESYSPROXY,HTTPPROXY,HTTPSPROXY,ALLPROXY,USEBGMAPI,USETMDBAPI,USEBANGUMIAPI,USEOPENAIAPI,OPENAI_BASE_URL,OPENAI_BASE_URLS,OPENAI_API_KEY,OPENAI_API_KEYS,OPENAI_API_KEY_ENV,OPENAI_MODEL,OPENAI_TIMEOUT_SECONDS,OPENAI_PRIORITY_FIRST,OPENAI_IDENTIFY_ALL,OPENAI_KEY_ROTATE_ON_STATUS,OPENAI_KEY_MAX_CONSECUTIVE_FAILURES,TMDB_BEARER_TOKEN,TMDB_BEARER_TOKEN_ENV,USELINK,STRICT_MODE,LINKFAILSUSEMOVEFLAGS,USETITLTOEP,PRINTLOGFLAG,RMLOGSFLAG,USEBOTFLAG,TIMELAPSE,SEEPSINGLECHARACTER,JELLYFINFORMAT,NOTLOADEXTLIST,MANDATORYCOVER,NETERRRECTRYTIMS,APIREQUESTSONLYUSECH,USEANIMETAG,NAMING_STYLE,CACHE_DIR,CACHE_TTL_SECONDS,CACHE_FLUSH_INTERVAL_SECONDS,DRY_RUN,MAX_FILENAME_LENGTH,OPERATION_LOG_DIR,OPERATION_LOG_ENABLE,OUTPUT_PATH,RUN_COMMAND,ROLLBACK_LOG_PATH,SCAN_SKIP_PATH_MARKERS,SCAN_SKIP_NAME_REGEX,LastPersistentCacheFlushTime,LastIdentificationIsMovie
    USEMODULE = None
    USEPROXY = True # 使用代理
    USESYSPROXY = True # 使用系统代理
    HTTPPROXY = 'http://127.0.0.1:7890' # Http代理
    HTTPSPROXY = 'http://127.0.0.1:7890' # Https代理
    ALLPROXY = '' # 全部代理
    USEBGMAPI = True # 使用BgmApi
    USETMDBAPI = True # 使用TMDBApi
    USEBANGUMIAPI = True # 使用BangumiApi (中文优化)
    USEOPENAIAPI = True # 使用OpenAI兼容Api进行名称识别
    OPENAI_BASE_URL = 'https://api.longcat.chat/openai' # OpenAI兼容接口地址
    OPENAI_BASE_URLS = '' # 多接口：逗号或 | 分隔；空则仅用 OPENAI_BASE_URL
    OPENAI_API_KEY = '' # 不建议写入仓库，请改用环境变量
    OPENAI_API_KEYS = '' # 多 Key：逗号或 | 分隔；空则仅用 OPENAI_API_KEY / 环境变量
    OPENAI_API_KEY_ENV = 'OPENAI_API_KEY' # 默认读取该环境变量
    OPENAI_MODEL = 'LongCat-Flash-Chat' # 模型名称
    OPENAI_TIMEOUT_SECONDS = 60 # OpenAI接口超时时间
    OPENAI_PRIORITY_FIRST = True # True时优先使用AI识别
    OPENAI_IDENTIFY_ALL = True # True时由AI直接识别剧名/季/集
    OPENAI_KEY_ROTATE_ON_STATUS = '401,429' # 触发切换下一接口/Key 的 HTTP 状态码
    OPENAI_KEY_MAX_CONSECUTIVE_FAILURES = 3 # 连续异常达此次数后切换槽位
    TMDB_BEARER_TOKEN = '' # 不建议写入仓库，请改用环境变量
    TMDB_BEARER_TOKEN_ENV = 'TMDB_BEARER_TOKEN' # 默认读取该环境变量
    USELINK = True # 使用硬链接开关
    STRICT_MODE = True # 严格模式：硬链接失败时不降级移动
    JELLYFINFORMAT = False # jellyfin 使用 ISO/639 标准 简体和繁体都使用chi做标识\
    USETITLTOEP = True # 给每个番剧视频加上番剧Title 
    LINKFAILSUSEMOVEFLAGS = False # 硬链接失败时是否使用MOVE
    PRINTLOGFLAG = True if __name__ == '__main__' else False# 打印log开关
    RMLOGSFLAG = 7 # 日志文件超时删除,填数字代表删除多久前的
    USEBOTFLAG = False # 使用TgBot进行通知
    TIMELAPSE = 0 # 延时处理番剧
    SEEPSINGLECHARACTER = False # SE EP单字符模式 01 -> 1
    NOTLOADEXTLIST = [] # 模块排除列表,格式 exmaple.py,XXXX.py + ,
    MANDATORYCOVER = True # 强制覆盖文件
    NETERRRECTRYTIMS = 2 # 网络请求错误时的重试次数
    APIREQUESTSONLYUSECH = False # Api请求只搜索中文部分
    USEANIMETAG = False # 使用番剧tag,带有anime标签的文件才会处理
    NAMING_STYLE = 'default' # default|emby
    CACHE_DIR = '.cache' # 持久化缓存目录
    CACHE_TTL_SECONDS = 86400 # 缓存有效期
    CACHE_FLUSH_INTERVAL_SECONDS = 60 # api_cache.json 定时刷盘秒数；0 表示仅退出时写入
    SCAN_SKIP_PATH_MARKERS = [] # 扫描忽略路径段；空列表使用内置默认
    SCAN_SKIP_NAME_REGEX = [] # 扫描忽略文件名正则字符串列表；空则使用内置默认
    LastPersistentCacheFlushTime = 0.0
    LastIdentificationIsMovie = False
    DRY_RUN = False # 仅预览不落盘
    MAX_FILENAME_LENGTH = 180 # Windows 路径留余量
    OPERATION_LOG_DIR = 'logs' # 操作日志目录
    OPERATION_LOG_ENABLE = True # 记录操作日志
    OUTPUT_PATH = '' # 可选输出目录，空值表示使用扫描目录
    RUN_COMMAND = 'process' # process|rollback
    ROLLBACK_LOG_PATH = ''

    Auxiliary_READConfig()
    Auxiliary_ApplyConfig()
    Auxiliary_InitRuntimeContext()
    Auxiliary_LoadManualWhitelist(force=True)
    Auxiliary_LoadPersistentCache()
    Auxiliary_Log((f'当前工具版本为{Versions}',f'当前操作系统识别码为{name},posix/nt/java对应linux/windows/java虚拟机'),'INFO')
    if DRY_RUN:
        Auxiliary_Log('当前处于 DRY_RUN 模式，所有操作仅预览不落盘','WARNING')
    if int(TIMELAPSE) != 0:
        Auxiliary_Log(f'正在{TIMELAPSE}秒延时中')
        sleep(int(TIMELAPSE))
    if USEMODULE == True:
        Auxiliary_LoadModule()
    if kwargs != {}:
        for i in kwargs:
            exec(f'global {i};{i} = {kwargs[i]}')
    return globals()

def AUxiliary_GetTag():
    '''获取Tag信息,判断处理模式'''
    def A(tag):
        if tag == 'anime':
            global USEANIMETAG
            USEANIMETAG = False  
        elif (X := search(r'AAM-(.*)',tag,flags=I)) != None:
                global animename
                animename = X.group(1)
                Auxiliary_Log(f'tag中指定了番剧名称 > {animename}')

    if tag and tag != '':
        if ',' not in tag :
            A(tag)
        elif ',' in tag:    
            for i in tag.split(','):
                A(i)
    if USEANIMETAG == True:
        Auxiliary_Exit('已开启USEANIMETAG配置,但不存在番剧Tag,正常退出')

def Start_GetArgv():
    '''获取参数,判断处理模式'''

    global filepath,filename,number,categoryname,animename,tag,DRY_RUN,NAMING_STYLE,OUTPUT_PATH,STRICT_MODE,USELINK,RUN_COMMAND,ROLLBACK_LOG_PATH
    if len(argv) == 1:
        Auxiliary_Help()

    if argv[1].lower() == 'rollback':
        RollbackParser = argparse.ArgumentParser(prog='AutoAnimeMv.py rollback', description='根据操作日志执行回滚')
        RollbackParser.add_argument('log_path', nargs='?', help='操作日志路径')
        RollbackParser.add_argument('--log', dest='log_opt', help='操作日志路径')
        Args = RollbackParser.parse_args(argv[2:])
        RollbackPath = Args.log_opt if Args.log_opt else Args.log_path
        if RollbackPath in [None, '']:
            Auxiliary_Exit('rollback 模式需要传入日志路径，例如: python AutoAnimeMv.py rollback --log operation.json')
        RUN_COMMAND = 'rollback'
        ROLLBACK_LOG_PATH = RollbackPath
        Auxiliary_InitRuntimeContext()
        return RollbackPath

    Parser = argparse.ArgumentParser(add_help=False)
    Parser.add_argument('filepath_pos', nargs='?')
    Parser.add_argument('filename_pos', nargs='?')
    Parser.add_argument('number_pos', nargs='?')
    Parser.add_argument('categoryname_pos', nargs='?')
    Parser.add_argument('tag_pos', nargs='?')
    Parser.add_argument('--filepath', dest='filepath_opt')
    Parser.add_argument('--filename', dest='filename_opt')
    Parser.add_argument('--number', dest='number_opt')
    Parser.add_argument('--categoryname', dest='categoryname_opt')
    Parser.add_argument('--animename', dest='animename_opt')
    Parser.add_argument('--tag', dest='tag_opt')
    Parser.add_argument('--dry-run', dest='dry_run_opt', action='store_true')
    Parser.add_argument('--naming-style', dest='naming_style_opt', choices=['default', 'emby'])
    Parser.add_argument('--output-path', dest='output_path_opt', help='整理输出目录路径')
    Parser.add_argument('--strict-mode', dest='strict_mode_opt', choices=['true', 'false'], help='严格模式开关')
    Parser.add_argument('--use-link', dest='force_use_link', action='store_true', help='强制使用硬链接')
    Parser.add_argument('--no-link', dest='force_no_link', action='store_true', help='禁用硬链接，使用移动')
    Parser.add_argument('-h', '--help', dest='show_help', action='store_true')
    Args, _ = Parser.parse_known_args(argv[1:])

    if Args.show_help:
        Auxiliary_Help()

    filepath = Args.filepath_opt if Args.filepath_opt else Args.filepath_pos
    filename = Args.filename_opt if Args.filename_opt else Args.filename_pos
    number = Args.number_opt if Args.number_opt else Args.number_pos
    categoryname = Args.categoryname_opt if Args.categoryname_opt else Args.categoryname_pos
    animename = Args.animename_opt if Args.animename_opt else None
    tag = Args.tag_opt if Args.tag_opt else Args.tag_pos

    if Args.naming_style_opt not in [None, '']:
        NAMING_STYLE = Args.naming_style_opt
    if Args.dry_run_opt:
        DRY_RUN = True
    if Args.output_path_opt not in [None, '']:
        OUTPUT_PATH = Args.output_path_opt
    if Args.strict_mode_opt not in [None, '']:
        STRICT_MODE = True if str(Args.strict_mode_opt).lower() == 'true' else False
    if Args.force_use_link:
        USELINK = True
    if Args.force_no_link:
        USELINK = False

    for Key in ['filepath','filename','number','categoryname','animename','tag','NAMING_STYLE','DRY_RUN','OUTPUT_PATH','STRICT_MODE','USELINK']:
        if Key in globals():
            Auxiliary_Log(f'{Key} < {globals()[Key]}')

    if filepath in [None, ''] or path.exists(filepath) == False:
        Auxiliary_Exit('请输入正确的处理目录路径')

    AUxiliary_GetTag()
    if filename not in [None, ''] and number not in [None, '']:
        return filepath,filename,number
    return filepath
    
        
# Processing 进行程序的开始工作,进行核心处理
def Processing_Mode(ArgvData:list):
    '''模式选择'''

    ArgvNumber = len(ArgvData) if type(ArgvData) in [list, tuple] else 1
    global Path,CategoryName
    Path = filepath
    CategoryName = categoryname
    Auxiliary_InitRuntimeContext()
    if path.exists(Path) == True:
        # 批处理模式(非分类|分类) or Qb下载模式
        if type(ArgvData) in [list, tuple] and ArgvNumber >= 3 and str(ArgvData[2]) == '1' and ArgvData[1] not in [None, '']:
            FileListTuporList = [ArgvData[1]]
        else:
            FileListTuporList = Auxiliary_ScanDIR(Path)
        Auxiliary_DeleteLogs()
        if CategoryName :
            Auxiliary_Log(f'当前分类 >> {CategoryName}')

        if type(FileListTuporList) == tuple:
            return FileListTuporList # 文件列表元组(视频文件列表,字幕文件列表)
        else:
            valid_files = []
            skipped_incomplete_files = []
            for i in FileListTuporList:
                if path.isfile(f'{Path}{Separator}{i}') == True:
                    if Auxiliary_IsIncompleteDownloadFile(i):
                        skipped_incomplete_files.append(i)
                        Auxiliary_Log(f'跳过未完成下载文件: {i}','INFO')
                        continue
                    valid_files.append(i)
                else:
                    Auxiliary_Log(f'{Path}{Separator}{i} 不存在的文件','WARNING')
            if valid_files != []:
                return valid_files  # 元组中唯一有效的文件列表
            if skipped_incomplete_files != []:
                Auxiliary_Log('本次仅检测到未完成下载文件，已全部跳过','INFO')
                return []
            Auxiliary_Exit('没有有效的番剧文件')
    else:
        Auxiliary_Exit(f'不存在 {Path} 目录')
   
def Processing_Main(LorT):
    '''核心处理'''
    global LastIdentificationFromAI,LastOpenAIFileInfoMeta,EpisodeDecisionDataCache

    SubtitleFiles = []
    if type(LorT) == tuple: # (视频文件列表,字幕文件列表)
        VideoFiles = LorT[0]
        SubtitleFiles = LorT[1]
    else: # 唯一有效的文件列表
        VideoFiles = LorT

    VideoFiles = sorted(VideoFiles, key=lambda X: Auxiliary_GetSourceFileMTime(X))
    for SourceFile in VideoFiles:
        File = path.basename(SourceFile)
        SourceAbsPath = Auxiliary_GetAbsoluteSourcePath(SourceFile)
        SourceMTime = Auxiliary_GetSourceFileMTime(SourceFile)
        LastOpenAIFileInfoMeta = {}
        if Auxiliary_IsIncompleteDownloadFile(File):
            Auxiliary_Log(f'跳过未完成下载文件: {SourceFile}','INFO')
            continue
        if Auxiliary_FileType(File) == 'ASS':
            Auxiliary_Log(f'跳过仅字幕文件主处理: {SourceFile}','INFO')
            continue
        PreDetectHint = Auxiliary_PreDetectEpisodeHint(File)
        if type(PreDetectHint) == dict and PreDetectHint.get('EpisodeKey') in EpisodeDecisionDataCache:
            ExistingDecision = EpisodeDecisionDataCache[PreDetectHint.get('EpisodeKey')]
            ExistingMTime = float(ExistingDecision.get('source_mtime', 0.0))
            if SourceMTime >= ExistingMTime:
                ExistingDst = ExistingDecision.get('dst', '')
                Auxiliary_Log(f'同集已保留更早文件，跳过较新重复资源: {SourceFile}','INFO')
                Auxiliary_RecordOperation('skip',SourceAbsPath,ExistingDst,'skipped','newer_duplicate_kept_oldest')
                continue
        flag = Processing_Identification(File)
        if flag == None:
            continue
        SE,EP,RAWSE,RAWEP,RAWName = flag
        NameEN = LastOpenAIFileInfoMeta.get('NameEN', '')
        NameRomaji = LastOpenAIFileInfoMeta.get('NameRomaji', '')
        CanonicalID = LastOpenAIFileInfoMeta.get('CanonicalID', '')
        HintCanonicalID = PreDetectHint.get('CanonicalID', '') if type(PreDetectHint) == dict else ''
        HintApiName = PreDetectHint.get('ApiName', '') if type(PreDetectHint) == dict else ''
        if 'animename' in globals() and animename not in ['',None]:
            ApiName = animename
            Auxiliary_Log('当前文件已由 OpenAI 识别季集，剧名使用手动指定 animename','INFO')
        else:
            ApiName = LastOpenAIFileInfoMeta.get('CanonicalZh') or RAWName
            if HintCanonicalID not in [None, ''] and CanonicalID not in [None, ''] and HintCanonicalID != CanonicalID:
                Auxiliary_Log(f'检测到单集剧名漂移，采用历史别名映射纠偏: {File}','WARNING')
                CanonicalID = HintCanonicalID
                if HintApiName not in [None, '']:
                    ApiName = HintApiName
                    RAWName = HintApiName
            elif HintCanonicalID not in [None, ''] and CanonicalID in [None, '']:
                CanonicalID = HintCanonicalID
                if HintApiName not in [None, ''] and Auxiliary_HasChineseText(str(ApiName)) == False:
                    ApiName = HintApiName
            if Auxiliary_HasChineseText(str(ApiName)) == False:
                Auxiliary_Log('剧名未收敛到中文','WARNING')
            else:
                Auxiliary_Log('OpenAI 识别与剧名链已完成','INFO')
        if NameEN in [None, ''] and Auxiliary_HasChineseText(RAWName) == False:
            NameEN = RAWName
        CanonicalSourceTag = 'openai_identify'
        CanonicalFromMainID, CanonicalFromMainZh = Auxiliary_UpsertCanonicalTitle(
            ApiName,
            NameEN,
            NameRomaji,
            CanonicalSourceTag,
            [RAWName, ApiName, File]
        )
        if CanonicalFromMainZh not in [None, '']:
            ApiName = CanonicalFromMainZh
        if CanonicalID in [None, ''] and CanonicalFromMainID not in [None, '']:
            CanonicalID = CanonicalFromMainID

        if CanonicalID not in [None, ''] and Auxiliary_ShowHasOrganizedEpisode(CanonicalID, SE, EP):
            Auxiliary_Log(
                f'跳过已整理剧集（ShowOrganizationIndex）: {Auxiliary_FormatOrganizedEpisodeTag(SE, EP)} << {ApiName}',
                'INFO'
            )
            Auxiliary_RecordOperation('skip', SourceAbsPath, '', 'skipped', 'already_organized_show_cache')
            continue

        EpisodeKey = Auxiliary_BuildEpisodeDecisionKey(ApiName, SE, EP, File)
        if EpisodeKey not in [None, ''] and EpisodeKey in EpisodeDecisionDataCache:
            ExistingDecision = EpisodeDecisionDataCache[EpisodeKey]
            ExistingMTime = float(ExistingDecision.get('source_mtime', 0.0))
            if SourceMTime >= ExistingMTime:
                ExistingDst = ExistingDecision.get('dst', '')
                Auxiliary_Log(f'同集已保留更早文件，跳过较新重复资源: {SourceFile}','INFO')
                Auxiliary_RecordOperation('skip',SourceAbsPath,ExistingDst,'skipped','newer_duplicate_kept_oldest')
                continue
        ASSList = Auxiliary_IDEASS(RAWName,RAWSE,RAWEP,SubtitleFiles) if SubtitleFiles != [] else None
        MainOperationResult = Sorting_Mv(File,RAWName,SE,EP,ASSList,ApiName,SourceFilePath=SourceFile)
        if Auxiliary_ShouldCacheResolvedFileInfo(MainOperationResult) and CanonicalID not in [None, '']:
            Auxiliary_ShowMarkOrganizedEpisode(CanonicalID, ApiName, NameEN, NameRomaji, SE, EP)
        if EpisodeKey not in [None, '']:
            ExistingDecision = EpisodeDecisionDataCache.get(EpisodeKey, {})
            ExistingMTime = float(ExistingDecision.get('source_mtime', 0.0)) if type(ExistingDecision) == dict else 0.0
            if type(ExistingDecision) != dict or ExistingDecision == {} or SourceMTime <= ExistingMTime:
                EpisodeDecisionDataCache[EpisodeKey] = {
                    'source_mtime': SourceMTime,
                    'src': str(SourceAbsPath),
                    'dst': MainOperationResult.get('dst', '') if type(MainOperationResult) == dict else '',
                    'resolved': {
                        'SE': str(SE),
                        'EP': str(EP),
                        'RAWSE': str(RAWSE),
                        'RAWEP': str(RAWEP),
                        'RAWName': str(RAWName),
                        'ApiName': str(ApiName),
                        'NameEN': str(NameEN) if NameEN not in [None, ''] else '',
                        'NameRomaji': str(NameRomaji) if NameRomaji not in [None, ''] else '',
                        'CanonicalID': str(CanonicalID) if CanonicalID not in [None, ''] else ''
                    }
                }

def Processing_Identification(File:str):
    '''识别：仅 OpenAI 全信息（季/集 + 剧名线索），失败则跳过当前文件并记入告警 JSON'''
    global LastIdentificationFromAI, LastOpenAIIdentifyFailure
    LastIdentificationFromAI = False

    NewFile = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(File)))
    AnimeFileCheckFlag = Auxiliary_AnimeFileCheck(NewFile)
    if AnimeFileCheckFlag != True:
        Auxiliary_Log(f'当前文件属于{AnimeFileCheckFlag},跳过处理','INFO')
        return None
    Auxiliary_Log('-'*80,'INFO')
    if USEOPENAIAPI != True or OPENAI_IDENTIFY_ALL != True:
        Auxiliary_Exit('必须启用 USEOPENAIAPI 与 OPENAI_IDENTIFY_ALL，由 OpenAI 识别季集与剧名线索')
    LastOpenAIIdentifyFailure = None
    OpenAIIdentifyData = Auxiliary_OpenAIIdentifyFileInfo(File)
    if OpenAIIdentifyData == None:
        BaseRow = {
            'input_basename': File,
            'stage': 'Processing_Identification',
        }
        if type(LastOpenAIIdentifyFailure) == dict:
            BaseRow.update(LastOpenAIIdentifyFailure)
        else:
            BaseRow['reason'] = 'openai_identify_returned_none'
            BaseRow['detail'] = 'Auxiliary_OpenAIIdentifyFileInfo 返回 None（可能为 mock 或未记录原因）'
        Auxiliary_AppendOpenAIIdentifyWarningLog(BaseRow)
        Auxiliary_Log(
            f'OpenAI 全信息识别失败，已跳过文件: {File}（明细已追加至 {Auxiliary_GetOpenAIIdentifyWarningLogPath().name}）',
            'WARNING'
        )
        return None
    LastIdentificationFromAI = True
    return OpenAIIdentifyData

def Auxiliary_SanitizePathComponent(Name, MaxLen=None):
    '''清洗文件名/目录名，避免 Windows 非法字符与保留名'''
    if Name in [None, '']:
        Name = 'Unknown'
    Name = Auxiliary_NormalizeDisplayTitle(Name).replace('\n', ' ').replace('\r', ' ')
    Name = sub(r'[<>:"/\\|?*\x00-\x1f]','_',Name)
    Name = sub(r'\s+',' ',Name).strip(' .')
    if Name == '':
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


def Auxiliary_SubtitleLanguageSuffixForEmby(ASSFileName):
    RawSuffix = Auxiliary_ASSFileCA(ASSFileName)
    Mapping = {
        '.chs': '.zh-CN',
        '.cht': '.zh-TW',
        '.jp': '.ja',
        '.other': '.und'
    }
    return Mapping.get(RawSuffix,'.und')


def Auxiliary_IsSamePhysicalFile(LeftPath, RightPath) -> bool:
    '''判断两个路径是否指向同一个物理文件（含硬链接）'''
    LeftPath = PathlibPath(LeftPath)
    RightPath = PathlibPath(RightPath)
    try:
        if LeftPath.exists() and RightPath.exists():
            return LeftPath.samefile(RightPath)
    except Exception:
        return False
    return False


def Auxiliary_MakeOperationResult(Action, SrcPath, DstPath, Status, Message='', BackupPath=''):
    return {
        'action':Action,
        'src':str(SrcPath),
        'dst':str(DstPath),
        'status':Status,
        'message':Message,
        'backup':str(BackupPath) if BackupPath not in [None, ''] else ''
    }


def Auxiliary_ExecuteFileOperation(SrcPath, DstPath):
    '''执行 move/link，支持 dry-run 与回滚备份'''
    SrcPath = PathlibPath(SrcPath)
    DstPath = PathlibPath(DstPath)
    BackupPath = ''
    ActionName = 'link' if USELINK == True else 'move'
    DryRunMode = Runtime.config.dry_run if 'Runtime' in globals() and Runtime else Auxiliary_ParseBool(DRY_RUN)
    StrictMode = Runtime.config.strict_mode if 'Runtime' in globals() and Runtime else Auxiliary_ParseBool(STRICT_MODE)

    if SrcPath.is_file() == False:
        Auxiliary_Log(f'源文件不存在，跳过: {SrcPath}','WARNING')
        Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'skipped','src_not_found')
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'src_not_found')

    if DstPath.exists():
        if Auxiliary_IsSamePhysicalFile(SrcPath, DstPath):
            Auxiliary_Log(f'目标文件已与源文件一致，跳过重复整理: {DstPath}','INFO')
            Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'skipped','same_file')
            return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'same_file')
        if USELINK == True:
            Auxiliary_Log(f'目标文件已存在，保留原有硬链接，跳过替换: {DstPath}','INFO')
            Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'skipped','existing_link_kept')
            return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'existing_link_kept')
        if MANDATORYCOVER != True:
            Auxiliary_Log(f'{DstPath}已存在,故跳过','WARNING')
            Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'skipped','target_exists')
            return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'skipped', 'target_exists')
        BackupPath = DstPath.with_name(f'{DstPath.name}.aam.bak.{CurrentRunID}')
        if DryRunMode == True:
            Auxiliary_Log(f'DRY_RUN: 预览覆盖备份 {DstPath} -> {BackupPath}','INFO')
        else:
            DstPath.parent.mkdir(parents=True,exist_ok=True)
            move(str(DstPath),str(BackupPath))
            Auxiliary_Log(f'覆盖前备份: {DstPath} -> {BackupPath}','INFO')

    if DryRunMode == True:
        Auxiliary_Log(f'DRY_RUN: 预览{ActionName.upper()} {SrcPath} -> {DstPath}','INFO')
        Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'dry-run','preview',BackupPath)
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'dry-run', 'preview', BackupPath)

    try:
        DstPath.parent.mkdir(parents=True,exist_ok=True)
        if USELINK == True:
            try:
                link(str(SrcPath),str(DstPath))
            except OSError as err:
                if '[WinError 1]' in str(err):
                    if StrictMode == True:
                        Auxiliary_Log('严格模式开启：硬链接失败后不会降级移动，已跳过当前文件','ERROR')
                        Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'failed','strict_mode_link_failed',BackupPath)
                        if BackupPath not in ['',None] and PathlibPath(BackupPath).exists():
                            try:
                                move(str(BackupPath),str(DstPath))
                            except Exception:
                                pass
                        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'failed', 'strict_mode_link_failed', BackupPath)
                    if LINKFAILSUSEMOVEFLAGS == True:
                        Auxiliary_Log('当前文件系统不支持硬链接，自动回退到 move','WARNING')
                        move(str(SrcPath),str(DstPath))
                        ActionName = 'move'
                    else:
                        raise err
                else:
                    raise err
        else:
            move(str(SrcPath),str(DstPath))
        Auxiliary_Log(f'{ActionName.upper()}-{DstPath} << {SrcPath}','INFO')
        Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'success','',BackupPath)
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'success', '', BackupPath)
    except Exception as err:
        if BackupPath not in ['',None] and PathlibPath(BackupPath).exists() and DstPath.exists() == False:
            try:
                move(str(BackupPath),str(DstPath))
            except Exception:
                pass
        Auxiliary_Log(f'文件操作失败 {SrcPath} -> {DstPath}: {err}','ERROR')
        Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'failed',str(err),BackupPath)
        return Auxiliary_MakeOperationResult(ActionName, SrcPath, DstPath, 'failed', str(err), BackupPath)


# Sorting 进行整理工作
def Sorting_Mv(FileName,RAWName,SE,EP,ASSList,ApiName,SourceFilePath=None):
    '''文件处理'''

    global CategoryName
    SourceFilePath = FileName if SourceFilePath in [None, ''] else SourceFilePath
    CategoryName = CategoryName if CategoryName else ''
    ApiName = ApiName if ApiName else RAWName
    NamingStyle = Runtime.config.naming_style if 'Runtime' in globals() and Runtime else str(NAMING_STYLE).strip().lower()
    NamingStyle = NamingStyle if NamingStyle in ['default','emby'] else 'default'
    DryRunMode = Runtime.config.dry_run if 'Runtime' in globals() and Runtime else Auxiliary_ParseBool(DRY_RUN)

    def PcSanitize(Component):
        return Auxiliary_SanitizePathComponent(Auxiliary_NormalizeChinesePunctuation(Component), MAX_FILENAME_LENGTH)

    SafeCategory = PcSanitize(CategoryName) if CategoryName != '' else ''
    SafeApiName = PcSanitize(ApiName)
    SEPad = Auxiliary_FormatSEEPToken(SE)
    EPPad = Auxiliary_FormatSEEPToken(EP)

    BaseDir = Runtime.output_path if 'Runtime' in globals() and Runtime else PathlibPath(Path)
    if SafeCategory != '':
        BaseDir = BaseDir / SafeCategory

    SeasonDirName = f'Season {SEPad}' if NamingStyle == 'emby' else f'Season{SE}'
    NewDir = BaseDir / SafeApiName / PcSanitize(SeasonDirName)
    if DryRunMode != True:
        NewDir.mkdir(parents=True,exist_ok=True)
    elif NewDir.exists():
        Auxiliary_Log(f'{NewDir}已存在','INFO')

    if NamingStyle == 'emby':
        EpisodeBaseName = f'{SafeApiName} - S{SEPad}E{EPPad}'
    else:
        EpisodeBaseName = f'S{SE}E{EP}' if USETITLTOEP != True else f'S{SE}E{EP}.{SafeApiName}'
    EpisodeBaseName = PcSanitize(EpisodeBaseName)

    if ASSList != None:
        for ASSFile in ASSList:
            FileType = path.splitext(ASSFile)[1].lower()
            ASSBaseName = path.basename(ASSFile)
            if NamingStyle == 'emby':
                NewASSName = PcSanitize(f'{SafeApiName} - S{SEPad}E{EPPad}{Auxiliary_SubtitleLanguageSuffixForEmby(ASSBaseName)}')
            else:
                NewASSName = PcSanitize(EpisodeBaseName + Auxiliary_ASSFileCA(ASSBaseName))
            DstPath = NewDir / f'{NewASSName}{FileType}'
            SrcPath = PathlibPath(Path) / ASSFile
            Auxiliary_ExecuteFileOperation(SrcPath,DstPath)

    FileType = path.splitext(FileName)[1].lower()
    if FileType in ['.ass','.srt']:
        if NamingStyle == 'emby':
            NewName = PcSanitize(f'{SafeApiName} - S{SEPad}E{EPPad}{Auxiliary_SubtitleLanguageSuffixForEmby(FileName)}')
        else:
            NewName = PcSanitize(EpisodeBaseName + Auxiliary_ASSFileCA(FileName))
    else:
        NewName = EpisodeBaseName
    DstPath = NewDir / f'{NewName}{FileType}'
    SrcPath = PathlibPath(Path) / SourceFilePath
    return Auxiliary_ExecuteFileOperation(SrcPath,DstPath)

# Auxiliary 其他辅助
def Auxiliary_Help(): # Help 
    global HelpMessages
    Logo = '''     
     █████╗ ██╗   ██╗████████╗ ██████╗  █████╗ ███╗   ██╗██╗███╗   ███╗███████╗███╗   ███╗██╗   ██╗
    ██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██╔══██╗████╗  ██║██║████╗ ████║██╔════╝████╗ ████║██║   ██║
    ███████║██║   ██║   ██║   ██║   ██║███████║██╔██╗ ██║██║██╔████╔██║█████╗  ██╔████╔██║██║   ██║
    ██╔══██║██║   ██║   ██║   ██║   ██║██╔══██║██║╚██╗██║██║██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║╚██╗ ██╔╝
    ██║  ██║╚██████╔╝   ██║   ╚██████╔╝██║  ██║██║ ╚████║██║██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║ ╚████╔╝ 
    ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝  ╚═══╝                                                                                            
    '''
    HelpMessages = '\n* 欢迎使用 AutoAnimeMv，这是一个用于番剧文件识别、重命名和整理的工具\n* 支持本地批处理、qBittorrent 回调、dry-run 预览与回滚\n* '
    print(Logo + '\n' + '-'*100 +  HelpMessages)
    quit()

def Auxiliary_LoadModule():
    ModuleFileList = []
    if path.exists('./Ext') == True:
        for FileName in listdir('./Ext'):
            File = path.splitext(FileName)
            if File[-1] == '.py' or File[-1] == '.PY':
                if File[0] in NOTLOADEXTLIST:
                    Auxiliary_Log(f'排除模块：{File[0]}')
                else:
                    Module = import_module(f'Ext.{File[0]}')
                    Auxiliary_Log(f'模块 << {File[0]}-v{Module.Versions}')
                    if '#' + File[0] in ConfigMagdict:
                        Module.main(globals(),ConfigMagdict[f'#{File[0]}'])
                    else:
                        Module.main(globals())
                    ModuleFileList.append(File[0])
                    # AAF = []
                    # for i in Module.ApplyAccessFun:
                    #     AAF.append(globals()[i])
                    # ReturnFun = Module.main(AAF)
                    #ModuleFileList[File[0]] = {'Versions':Module.Versions,'ApplyAccessFun':Module.ApplyAccessFun,'ApplyChangeFun':Module.ApplyChangeFun,'Module':Module,'ReturnFun':ReturnFun}
            #elif File[-1] == '.ini' or File[-1] == '.INI':
        if ModuleFileList != {}:
            Auxiliary_Log(f'加载{len(ModuleFileList)}个可加载模块 >> {ModuleFileList}')       
        else:
            Auxiliary_Log('无扩展')
    else:
        Auxiliary_Log('不存在扩展文件夹 ./Ext')

def Auxiliary_NormalizeConfigSection(section_name):
    '''规范化配置分区名称'''
    section_name = section_name.strip()
    if section_name.lower() in ['settings', 'config', '#config']:
        return '#Config'
    return section_name

def Auxiliary_ParseConfigValue(ConfigValue):
    '''解析配置值，兼容字符串/布尔/数字/列表'''
    ConfigValue = ConfigValue.strip()
    if ConfigValue == '':
        return ''
    LowerValue = ConfigValue.lower()
    if LowerValue == 'true':
        return True
    if LowerValue == 'false':
        return False
    if LowerValue in ['none', 'null']:
        return None
    try:
        return literal_eval(ConfigValue)
    except Exception:
        return ConfigValue

def Auxiliary_MaskConfigValue(ConfigName, ConfigValue):
    '''日志打印时对敏感配置做脱敏'''
    if search(r'key|token|secret|password', ConfigName, flags=I) != None:
        ConfigValue = '' if ConfigValue is None else str(ConfigValue)
        if ConfigValue == '':
            return ''
        if len(ConfigValue) <= 8:
            return '***'
        return f'{ConfigValue[:3]}***{ConfigValue[-2:]}'
    return ConfigValue

def Auxiliary_READConfig():
    '''读取外置Config.ini文件并更新'''

    global ConfigMagdict
    if path.isfile((X := f'{PyPath}{Separator}config.ini')):
        with open(X,'r',encoding='UTF-8') as ff:
            Auxiliary_Log('正在读取外置ini文件','INFO')
            ConfigMagdict = {}
            KeyName = None
            for i in ff.readlines():
                i = i.strip('\n').strip()
                if i == '' or i[0] == ';':
                    continue
                if findall(r'\[(.*?)\]',i) != []:
                    KeyName = Auxiliary_NormalizeConfigSection(findall(r'\[(.*?)\]',i)[0])
                    if KeyName not in ConfigMagdict:
                        ConfigMagdict[KeyName] = {}
                elif i[0] != '#':
                    if KeyName == None:
                        Auxiliary_Log(f'跳过未归属分区的配置行: {i}','WARNING')
                        continue
                    if '=' not in i:
                        Auxiliary_Log(f'跳过不合法配置行: {i}','WARNING')
                        continue
                    ConfigItem = i.split("=",1)
                    ConfigMagdict[KeyName][ConfigItem[0].strip('- ')] = ConfigItem[1].strip('- ')
        if ConfigMagdict != {}:
            ConfigSummary = {section:list(values.keys()) for section,values in ConfigMagdict.items()}
            Auxiliary_Log(f'读取到配置分区: {ConfigSummary}')
        else:
            Auxiliary_Log('外置ini文件没有配置','WARNING')

def Auxiliary_ApplyConfig():
    if 'ConfigMagdict' in globals() and '#Config' in ConfigMagdict:
        for ConfigName in ConfigMagdict['#Config']:
            ConfigValue = Auxiliary_ParseConfigValue(ConfigMagdict['#Config'][ConfigName])
            globals()[ConfigName] = ConfigValue
            Auxiliary_Log(f'配置 < {ConfigName} = {Auxiliary_MaskConfigValue(ConfigName,ConfigValue)}','INFO')
        Auxiliary_PROXY()


def Auxiliary_ParseBool(Value) -> bool:
    if type(Value) == bool:
        return Value
    if type(Value) in [int, float]:
        return Value != 0
    if Value is None:
        return False
    return str(Value).strip().lower() in ['true', '1', 'yes', 'y', 'on']


def Auxiliary_ParseInt(Value, DefaultValue) -> int:
    try:
        Parsed = int(Value)
        return Parsed
    except Exception:
        return DefaultValue


def Auxiliary_InitRuntimeContext():
    '''初始化运行时上下文'''
    global Runtime
    CacheTTL = Auxiliary_ParseInt(CACHE_TTL_SECONDS, 86400)
    if CacheTTL < 0:
        CacheTTL = 86400
    NamingStyle = str(NAMING_STYLE).strip().lower() if NAMING_STYLE not in [None, ''] else 'default'
    if NamingStyle not in ['default', 'emby']:
        NamingStyle = 'default'
    CategoryNameValue = categoryname if 'categoryname' in globals() and categoryname not in [None, ''] else ''
    SourcePath = filepath if 'filepath' in globals() and filepath not in [None, ''] else PyPath
    OutputPathValue = OUTPUT_PATH if 'OUTPUT_PATH' in globals() and OUTPUT_PATH not in [None, ''] else SourcePath
    OutputPathObj = PathlibPath(OutputPathValue)
    if OutputPathObj.is_absolute() == False:
        OutputPathObj = PathlibPath(SourcePath) / OutputPathObj
    Runtime = RuntimeContext(
        source_path=PathlibPath(SourcePath),
        output_path=OutputPathObj,
        category_name=CategoryNameValue,
        config=Config(
            naming_style=NamingStyle,
            dry_run=Auxiliary_ParseBool(DRY_RUN),
            cache_dir=str(CACHE_DIR).strip() if CACHE_DIR not in [None, ''] else '.cache',
            cache_ttl_seconds=CacheTTL,
            tmdb_token_env=str(TMDB_BEARER_TOKEN_ENV).strip() if TMDB_BEARER_TOKEN_ENV not in [None, ''] else 'TMDB_BEARER_TOKEN',
            openai_key_env=str(OPENAI_API_KEY_ENV).strip() if OPENAI_API_KEY_ENV not in [None, ''] else 'OPENAI_API_KEY',
            openai_identify_all=Auxiliary_ParseBool(OPENAI_IDENTIFY_ALL),
            strict_mode=Auxiliary_ParseBool(STRICT_MODE),
            output_path=str(OutputPathObj)
        )
    )
    if OPERATION_LOG_ENABLE:
        LogBasePath = Runtime.source_path
        if LogBasePath.exists() == False:
            LogBasePath = PathlibPath(PyPath)
        OpDirName = str(OPERATION_LOG_DIR).strip() if OPERATION_LOG_DIR not in [None, ''] else 'logs'
        Runtime.operation_log_path = LogBasePath / OpDirName / f'AutoAnime_operations_{CurrentRunID}.json'
    if RUN_COMMAND == 'rollback' and ROLLBACK_LOG_PATH not in [None, '']:
        Runtime.rollback_log_path = PathlibPath(ROLLBACK_LOG_PATH)


def Auxiliary_GetCacheStorePath() -> PathlibPath:
    if 'Runtime' in globals() and Runtime and Runtime.config and Runtime.config.cache_dir not in [None, '']:
        CacheDir = Runtime.config.cache_dir
    else:
        CacheDir = '.cache'
    CacheBasePath = PathlibPath(CacheDir)
    if CacheBasePath.is_absolute() == False:
        CacheBasePath = PathlibPath(PyPath) / CacheBasePath
    if CacheBasePath.exists() == False:
        CacheBasePath.mkdir(parents=True, exist_ok=True)
    return CacheBasePath / 'api_cache.json'


def Auxiliary_ParseDelimitedConfigList(ConfigValue):
    if ConfigValue in [None, '']:
        return []
    if type(ConfigValue) == list:
        return [str(Item).strip() for Item in ConfigValue if str(Item).strip() not in [None, '']]
    RawText = str(ConfigValue).strip()
    if RawText == '':
        return []
    for Delimiter in ['|', ',', '\n', ';']:
        if Delimiter in RawText:
            return [Part.strip() for Part in RawText.replace('\r', '').split(Delimiter) if Part.strip() not in [None, '']]
    return [RawText]


def Auxiliary_DefaultScanSkipPathMarkers():
    return [
        'SP', 'SPs', 'OP', 'ED', 'PV', 'PVs', 'NCOP', 'NCED', 'NCOPs', 'NCEDs',
        'Special', 'Specials', 'Extra', 'Extras', 'Bonus', 'Menus', 'Menu',
        'Creditless', 'Clean', 'CM', 'Preview', 'Previews', 'Trailer', 'Teasers',
        'Scans', 'Scan', 'Making', 'Interview', 'Tokuten', 'Drama'
    ]


def Auxiliary_DefaultScanSkipNameRegexStrings():
    return [
        r'(?i)\bNCOP\d*\b',
        r'(?i)\bNCED\d*\b',
        r'(?i)Non-?Credit',
        r'(?i)\bMenu\d+\b',
        r'(?i)\b(PV|Preview|CM)\d*\b',
    ]


def Auxiliary_GetScanSkipPathMarkers():
    Markers = SCAN_SKIP_PATH_MARKERS if 'SCAN_SKIP_PATH_MARKERS' in globals() else []
    if type(Markers) != list or Markers == []:
        return Auxiliary_DefaultScanSkipPathMarkers()
    return [str(M).strip() for M in Markers if str(M).strip() not in [None, '']]


def Auxiliary_GetScanSkipNameRegexList():
    Patterns = SCAN_SKIP_NAME_REGEX if 'SCAN_SKIP_NAME_REGEX' in globals() else []
    if type(Patterns) != list or Patterns == []:
        PatternStrings = Auxiliary_DefaultScanSkipNameRegexStrings()
    else:
        PatternStrings = [str(P).strip() for P in Patterns if str(P).strip() not in [None, '']]
    CompiledList = []
    for PatternStr in PatternStrings:
        try:
            CompiledList.append(compile(PatternStr))
        except Exception:
            continue
    return CompiledList


def Auxiliary_ScanEntryShouldSkip(RelativeFileNormalized, BaseName):
    PathLower = RelativeFileNormalized.lower()
    Segments = [Seg for Seg in PathLower.split('/') if Seg not in [None, '']]
    MarkerList = [M.lower() for M in Auxiliary_GetScanSkipPathMarkers()]
    for Segment in Segments[:-1]:
        for Marker in MarkerList:
            if Marker in [None, '']:
                continue
            if Segment == Marker or Segment.startswith(Marker + '.') or Segment.startswith(Marker + '_'):
                return True
    for RegexObj in Auxiliary_GetScanSkipNameRegexList():
        try:
            if RegexObj.search(BaseName) != None:
                return True
        except Exception:
            continue
    return False


def Auxiliary_GetOpenAIRuntimeStatePath() -> PathlibPath:
    return Auxiliary_GetCacheStorePath().parent / 'openai_runtime_state.json'


def Auxiliary_LoadOpenAIRuntimeState():
    StatePath = Auxiliary_GetOpenAIRuntimeStatePath()
    if StatePath.is_file() == False:
        return {'active_slot_index': 0, 'updated_at': 0.0}
    try:
        with open(StatePath, 'r', encoding='UTF-8') as StateFile:
            Data = json.load(StateFile)
        if type(Data) != dict:
            return {'active_slot_index': 0, 'updated_at': 0.0}
        IndexValue = Auxiliary_ParseInt(Data.get('active_slot_index', 0), 0)
        if IndexValue < 0:
            IndexValue = 0
        return {'active_slot_index': IndexValue, 'updated_at': float(Data.get('updated_at', 0.0) or 0.0)}
    except Exception:
        return {'active_slot_index': 0, 'updated_at': 0.0}


def Auxiliary_SaveOpenAIRuntimeState(StateDict):
    StatePath = Auxiliary_GetOpenAIRuntimeStatePath()
    try:
        StatePath.parent.mkdir(parents=True, exist_ok=True)
        Payload = {
            'active_slot_index': int(StateDict.get('active_slot_index', 0)),
            'updated_at': time()
        }
        with open(StatePath, 'w', encoding='UTF-8') as StateFile:
            json.dump(Payload, StateFile, ensure_ascii=False, indent=2)
    except Exception as err:
        Auxiliary_Log(f'OpenAI 运行时状态写入失败: {err}', 'WARNING')


def Auxiliary_GetOpenAIEndpointSlots():
    UrlList = Auxiliary_ParseDelimitedConfigList(OPENAI_BASE_URLS if 'OPENAI_BASE_URLS' in globals() else '')
    if UrlList == []:
        BaseFallback = OPENAI_BASE_URL if 'OPENAI_BASE_URL' in globals() and OPENAI_BASE_URL not in [None, ''] else ''
        UrlList = [str(BaseFallback).strip()] if str(BaseFallback).strip() not in [None, ''] else []
    KeyList = Auxiliary_ParseDelimitedConfigList(OPENAI_API_KEYS if 'OPENAI_API_KEYS' in globals() else '')
    if KeyList == []:
        SingleKey = Auxiliary_GetOpenAIApiKey()
        KeyList = [SingleKey] if SingleKey not in [None, ''] else []
    if UrlList == [] or KeyList == []:
        return []
    SlotCount = max(len(UrlList), len(KeyList))
    Slots = []
    for SlotIndex in range(SlotCount):
        UrlItem = UrlList[SlotIndex % len(UrlList)].rstrip('/')
        KeyItem = KeyList[SlotIndex % len(KeyList)]
        Slots.append((UrlItem, KeyItem))
    return Slots


def Auxiliary_ParseOpenAIRotateStatusCodes():
    RawText = str(OPENAI_KEY_ROTATE_ON_STATUS).strip() if 'OPENAI_KEY_ROTATE_ON_STATUS' in globals() else '401,429'
    Codes = set()
    for Part in RawText.replace('|', ',').split(','):
        Part = Part.strip()
        if Part.isdigit():
            Codes.add(int(Part))
    if Codes == set():
        Codes = {401, 429}
    return Codes


def Auxiliary_OpenAIHttpBodyIndicatesQuota(ResponseText):
    if ResponseText in [None, '']:
        return False
    LowerText = str(ResponseText).lower()
    return 'insufficient_quota' in LowerText or 'rate_limit' in LowerText or 'billing' in LowerText


def Auxiliary_OpenAIChatCompletionsPost(RequestJson):
    Slots = Auxiliary_GetOpenAIEndpointSlots()
    if Slots == []:
        return None
    StateSnapshot = Auxiliary_LoadOpenAIRuntimeState()
    StartIndex = Auxiliary_ParseInt(StateSnapshot.get('active_slot_index', 0), 0) % len(Slots)
    TimeoutSeconds = Auxiliary_ParseInt(OPENAI_TIMEOUT_SECONDS, 60) if 'OPENAI_TIMEOUT_SECONDS' in globals() else 60
    if TimeoutSeconds <= 0:
        TimeoutSeconds = 60
    RetryTimes = Auxiliary_ParseInt(NETERRRECTRYTIMS, 2) if 'NETERRRECTRYTIMS' in globals() else 2
    if RetryTimes < 0:
        RetryTimes = 0
    RotateCodes = Auxiliary_ParseOpenAIRotateStatusCodes()
    MaxConsecutive = Auxiliary_ParseInt(OPENAI_KEY_MAX_CONSECUTIVE_FAILURES, 3) if 'OPENAI_KEY_MAX_CONSECUTIVE_FAILURES' in globals() else 3
    if MaxConsecutive <= 0:
        MaxConsecutive = 1

    for SlotOffset in range(len(Slots)):
        SlotIndex = (StartIndex + SlotOffset) % len(Slots)
        BaseUrl, ApiKey = Slots[SlotIndex]
        if ApiKey in [None, '']:
            continue
        ConsecutiveFailures = 0
        for RetryIndex in range(RetryTimes + 1):
            HttpData = None
            try:
                HttpData = post(
                    f'{BaseUrl.rstrip("/")}/v1/chat/completions',
                    json=RequestJson,
                    headers={
                        'Authorization': f'Bearer {ApiKey}',
                        'Content-Type': 'application/json',
                        'User-Agent': f'AutoAnimeMv/{Versions}'
                    },
                    timeout=TimeoutSeconds
                )
            except exceptions.RequestException as err:
                ConsecutiveFailures += 1
                if RetryIndex < RetryTimes:
                    Auxiliary_Log(f'OpenAI 请求异常，槽位 {SlotIndex+1}/{len(Slots)} 第{RetryIndex+1}/{RetryTimes+1}次重试: {err}', 'WARNING')
                    continue
                Auxiliary_Log(f'OpenAI 请求失败，槽位 {SlotIndex+1}/{len(Slots)}: {err}', 'WARNING')
                break
            if HttpData.status_code == 200:
                StateSnapshot['active_slot_index'] = SlotIndex
                Auxiliary_SaveOpenAIRuntimeState(StateSnapshot)
                return HttpData
            ResponseText = ''
            try:
                ResponseText = HttpData.text
            except Exception:
                ResponseText = ''
            if HttpData.status_code in RotateCodes or Auxiliary_OpenAIHttpBodyIndicatesQuota(ResponseText):
                Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 返回 {HttpData.status_code}，切换下一槽位', 'WARNING')
                break
            ConsecutiveFailures += 1
            if RetryIndex < RetryTimes:
                Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 状态码 {HttpData.status_code}，重试 {RetryIndex+1}/{RetryTimes+1}', 'WARNING')
                continue
            Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 状态码 {HttpData.status_code}，放弃本槽位', 'WARNING')
            break
        if ConsecutiveFailures >= MaxConsecutive:
            Auxiliary_Log(f'OpenAI 槽位 {SlotIndex+1}/{len(Slots)} 连续失败达 {MaxConsecutive}，尝试下一槽位', 'WARNING')
    return None


def Auxiliary_MaybeFlushPersistentCache():
    global LastPersistentCacheFlushTime
    Interval = Auxiliary_ParseInt(CACHE_FLUSH_INTERVAL_SECONDS, 60) if 'CACHE_FLUSH_INTERVAL_SECONDS' in globals() else 60
    if Interval <= 0:
        return
    if PersistentApiCacheDirty != True:
        return
    NowTs = time()
    if NowTs - float(LastPersistentCacheFlushTime or 0.0) < float(Interval):
        return
    Auxiliary_SavePersistentCache(force=True)
    LastPersistentCacheFlushTime = NowTs


def Auxiliary_GetManualWhitelistPath() -> PathlibPath:
    CacheDirPath = Auxiliary_GetCacheStorePath().parent
    return CacheDirPath / 'manual_title_whitelist.json'


def Auxiliary_LoadManualWhitelist(force=False):
    global ManualTitleWhitelistDataCache,ManualTitleWhitelistMTime
    DefaultWhitelist = {
        'mao': '摩绪',
    }
    WhitelistPath = Auxiliary_GetManualWhitelistPath()
    if WhitelistPath.exists() == False:
        try:
            with open(WhitelistPath, 'w', encoding='UTF-8') as f:
                json.dump(DefaultWhitelist, f, ensure_ascii=False, indent=2)
            ManualTitleWhitelistDataCache = DefaultWhitelist.copy()
            ManualTitleWhitelistMTime = float(WhitelistPath.stat().st_mtime)
            Auxiliary_Log(f'已创建手工白名单文件: {WhitelistPath}','INFO')
            return ManualTitleWhitelistDataCache
        except Exception as err:
            Auxiliary_Log(f'创建手工白名单文件失败，将使用内置默认值: {err}','WARNING')
            ManualTitleWhitelistDataCache = DefaultWhitelist.copy()
            ManualTitleWhitelistMTime = 0.0
            return ManualTitleWhitelistDataCache

    try:
        FileMTime = float(WhitelistPath.stat().st_mtime)
    except Exception:
        FileMTime = 0.0
    if force != True and type(ManualTitleWhitelistDataCache) == dict and ManualTitleWhitelistDataCache != {} and ManualTitleWhitelistMTime == FileMTime:
        return ManualTitleWhitelistDataCache

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
        ManualTitleWhitelistDataCache = LoadedWhitelist
        ManualTitleWhitelistMTime = FileMTime
        return ManualTitleWhitelistDataCache
    except Exception as err:
        Auxiliary_Log(f'读取手工白名单文件失败，将使用内置默认值: {err}','WARNING')
        ManualTitleWhitelistDataCache = DefaultWhitelist.copy()
        ManualTitleWhitelistMTime = 0.0
        return ManualTitleWhitelistDataCache


def Auxiliary_LoadPersistentCache():
    global PersistentApiCache,PersistentApiCacheDirty
    CacheFilePath = Auxiliary_GetCacheStorePath()
    PersistentApiCache = {}
    PersistentApiCacheDirty = False
    if CacheFilePath.is_file():
        try:
            with open(CacheFilePath,'r',encoding='UTF-8') as CacheFile:
                CacheData = json.load(CacheFile)
            if type(CacheData) == dict:
                PersistentApiCache = CacheData
                Auxiliary_Log(f'已加载持久化缓存文件 {CacheFilePath}','INFO')
        except Exception as err:
            Auxiliary_Log(f'缓存文件读取失败，将使用空缓存: {err}','WARNING')
            PersistentApiCache = {}
    Auxiliary_RebuildCanonicalIndexesFromPersistentCache()


def Auxiliary_SavePersistentCache(force=False):
    global PersistentApiCacheDirty
    if force != True and PersistentApiCacheDirty != True:
        return
    CacheFilePath = Auxiliary_GetCacheStorePath()
    try:
        with open(CacheFilePath,'w',encoding='UTF-8') as CacheFile:
            json.dump(PersistentApiCache,CacheFile,ensure_ascii=False,indent=2,sort_keys=True)
        PersistentApiCacheDirty = False
        Auxiliary_Log(f'持久化缓存写入完成 {CacheFilePath}','INFO')
    except Exception as err:
        Auxiliary_Log(f'持久化缓存写入失败: {err}','WARNING')


def Auxiliary_GetPersistentCache(CacheGroup, CacheKey):
    global PersistentApiCache,PersistentApiCacheDirty
    if CacheGroup not in PersistentApiCache:
        return None
    GroupCache = PersistentApiCache[CacheGroup]
    if type(GroupCache) != dict or CacheKey not in GroupCache:
        return None
    CacheRecord = GroupCache[CacheKey]
    if type(CacheRecord) != dict:
        return None
    CacheValue = CacheRecord.get('value')
    CacheTimestamp = CacheRecord.get('ts', 0)
    NeverExpireGroups = {'TitleAliasIndex','CanonicalTitleIndex','ShowOrganizationIndex'}
    if CacheGroup in NeverExpireGroups:
        TTLValue = 0
    else:
        TTLValue = Runtime.config.cache_ttl_seconds if 'Runtime' in globals() and Runtime else 86400
    if TTLValue > 0 and (time() - float(CacheTimestamp)) > TTLValue:
        try:
            del GroupCache[CacheKey]
            PersistentApiCacheDirty = True
        except Exception:
            pass
        return None
    return CacheValue


def Auxiliary_SetPersistentCache(CacheGroup, CacheKey, CacheValue):
    global PersistentApiCache,PersistentApiCacheDirty
    if CacheGroup not in PersistentApiCache or type(PersistentApiCache[CacheGroup]) != dict:
        PersistentApiCache[CacheGroup] = {}
    PersistentApiCache[CacheGroup][CacheKey] = {'value':CacheValue,'ts':time()}
    PersistentApiCacheDirty = True


def Auxiliary_GetTMDBBearerToken():
    TokenValue = TMDB_BEARER_TOKEN if 'TMDB_BEARER_TOKEN' in globals() else ''
    if TokenValue not in [None, '']:
        return str(TokenValue).strip()
    EnvName = Runtime.config.tmdb_token_env if 'Runtime' in globals() and Runtime else TMDB_BEARER_TOKEN_ENV
    EnvName = str(EnvName).strip() if EnvName not in [None, ''] else 'TMDB_BEARER_TOKEN'
    return str(environ.get(EnvName, '')).strip()


def Auxiliary_GetOpenAIApiKey():
    ApiKey = OPENAI_API_KEY if 'OPENAI_API_KEY' in globals() else ''
    if ApiKey not in [None, '']:
        return str(ApiKey).strip()
    EnvName = Runtime.config.openai_key_env if 'Runtime' in globals() and Runtime else OPENAI_API_KEY_ENV
    EnvName = str(EnvName).strip() if EnvName not in [None, ''] else 'OPENAI_API_KEY'
    return str(environ.get(EnvName, '')).strip()


def Auxiliary_QueryTMDBChineseTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''仅通过 TMDB 查询中文标题；未命中中文时返回 None'''
    global USETMDBAPI,TMDBAPIDataCache
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    CandidateEn = Auxiliary_NormalizeDisplayTitle(CandidateEn)
    CandidateRomaji = Auxiliary_NormalizeDisplayTitle(CandidateRomaji)
    if QueryName in [None, ''] or USETMDBAPI != True:
        return None

    CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(
        QueryName,
        CandidateEn,
        CandidateRomaji
    )
    if CanonicalZh not in [None, '']:
        return CanonicalZh
    if Auxiliary_GetTMDBBearerToken() in [None, '']:
        Auxiliary_Log('TMDBApi 已启用但未配置 token，跳过 TMDB 查询','WARNING')
        return None

    CandidateKeys = Auxiliary_GetStandardTitleCacheCandidates(QueryName)
    if QueryName not in CandidateKeys:
        CandidateKeys.insert(0, QueryName)
    for CacheKey in CandidateKeys:
        CacheValue = None
        if type(TMDBAPIDataCache) == dict and CacheKey in TMDBAPIDataCache:
            CacheValue = TMDBAPIDataCache.get(CacheKey)
            Auxiliary_Log(f'{CacheValue} << TMDB内存缓存查询结果')
        else:
            CacheValue = Auxiliary_GetPersistentCache('TMDB', CacheKey)
            if CacheValue not in [None, '']:
                if type(TMDBAPIDataCache) != dict:
                    TMDBAPIDataCache = {}
                TMDBAPIDataCache[CacheKey] = CacheValue
                Auxiliary_Log(f'{CacheValue} << TMDB持久化缓存查询结果')
        CacheValue = Auxiliary_NormalizeApiTitle(CacheValue)
        if CacheValue in [None, ''] or Auxiliary_HasChineseText(CacheValue) == False:
            continue
        return CacheValue

    TMDBApiData = Auxiliary_Http(
        f'https://api.themoviedb.org/3/search/tv?query={quote(QueryName)}&include_adult=true&language=zh&page=1',
        ResponseType='json',
        Timeout=20
    )
    if type(TMDBApiData) != dict:
        Auxiliary_Log(f'TMDBApi返回异常: {QueryName}','WARNING')
        return None
    ResultList = TMDBApiData.get('results', [])
    if type(ResultList) != list or ResultList == []:
        Auxiliary_Log(f'TMDBApi没有检索到关于 {QueryName} 内容','WARNING')
        return None

    ApiTitle = ''
    for ResultItem in ResultList:
        if type(ResultItem) != dict:
            continue
        CandidateTitle = Auxiliary_NormalizeApiTitle(ResultItem.get('name') or ResultItem.get('original_name') or '')
        if CandidateTitle not in [None, ''] and Auxiliary_HasChineseText(CandidateTitle):
            ApiTitle = CandidateTitle
            break
    if ApiTitle in [None, '']:
        Auxiliary_Log(f'TMDBApi命中结果但未返回中文标题: {QueryName}','WARNING')
        return None

    CandidateEnForUpsert = CandidateEn
    if CandidateEnForUpsert in [None, ''] and Auxiliary_HasChineseText(QueryName) == False:
        CandidateEnForUpsert = QueryName
    CandidateAliases = [QueryName, CandidateEn, CandidateRomaji]
    if type(AliasList) == list:
        CandidateAliases.extend(AliasList)
    CandidateAliases = [Auxiliary_NormalizeDisplayTitle(Item) for Item in CandidateAliases if Item not in [None, '']]

    _, CanonicalTitle = Auxiliary_UpsertCanonicalTitle(
        ApiTitle,
        CandidateEnForUpsert,
        CandidateRomaji,
        'TMDB',
        CandidateAliases
    )
    if CanonicalTitle not in [None, ''] and Auxiliary_HasChineseText(CanonicalTitle):
        ApiTitle = CanonicalTitle
    for CacheKey in CandidateKeys:
        TMDBAPIDataCache[CacheKey] = ApiTitle
        Auxiliary_SetPersistentCache('TMDB', CacheKey, ApiTitle)
    Auxiliary_Log(f'{ApiTitle} << TMDBApi查询结果')
    return ApiTitle


def Auxiliary_QueryTMDBEnglishTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''TMDB en-US 检索，返回英文剧名（不要求中文）'''
    global USETMDBAPI,TMDBAPIDataCache
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    CandidateEn = Auxiliary_NormalizeDisplayTitle(CandidateEn)
    CandidateRomaji = Auxiliary_NormalizeDisplayTitle(CandidateRomaji)
    if QueryName in [None, ''] or USETMDBAPI != True:
        return None
    if Auxiliary_GetTMDBBearerToken() in [None, '']:
        Auxiliary_Log('TMDBApi 已启用但未配置 token，跳过 TMDB 英文查询','WARNING')
        return None
    CandidateKeys = Auxiliary_GetStandardTitleCacheCandidates(QueryName)
    if QueryName not in CandidateKeys:
        CandidateKeys.insert(0, QueryName)
    for CacheKey in CandidateKeys:
        RawVal = None
        if type(TMDBAPIDataCache) == dict and f'en:{CacheKey}' in TMDBAPIDataCache:
            RawVal = TMDBAPIDataCache.get(f'en:{CacheKey}')
        else:
            Group = PersistentApiCache.get('TMDB_EN', {}) if type(PersistentApiCache) == dict else {}
            Rec = Group.get(CacheKey) if type(Group) == dict else None
            if type(Rec) == dict and Rec.get('value') not in [None, '']:
                RawVal = Rec.get('value')
        if RawVal not in [None, '']:
            return Auxiliary_NormalizeDisplayTitle(str(RawVal))
    TMDBApiData = Auxiliary_Http(
        f'https://api.themoviedb.org/3/search/tv?query={quote(QueryName)}&include_adult=true&language=en-US&page=1',
        ResponseType='json',
        Timeout=20
    )
    if type(TMDBApiData) != dict:
        Auxiliary_Log(f'TMDBApi(EN)返回异常: {QueryName}','WARNING')
        return None
    ResultList = TMDBApiData.get('results', [])
    if type(ResultList) != list or ResultList == []:
        Auxiliary_Log(f'TMDBApi(EN)没有检索到关于 {QueryName} 内容','WARNING')
        return None
    ApiTitle = ''
    for ResultItem in ResultList:
        if type(ResultItem) != dict:
            continue
        ApiTitle = Auxiliary_NormalizeDisplayTitle(ResultItem.get('name') or ResultItem.get('original_name') or '')
        if ApiTitle not in [None, '']:
            break
    if ApiTitle in [None, '']:
        return None
    CandidateAliases = [QueryName, CandidateEn, CandidateRomaji]
    if type(AliasList) == list:
        CandidateAliases.extend(AliasList)
    CandidateAliases = [Auxiliary_NormalizeDisplayTitle(Item) for Item in CandidateAliases if Item not in [None, '']]
    EnForUpsert = CandidateEn if CandidateEn not in [None, ''] else ApiTitle
    Auxiliary_UpsertCanonicalTitle(
        '',
        EnForUpsert if EnForUpsert not in [None, ''] else ApiTitle,
        CandidateRomaji,
        'TMDB',
        CandidateAliases + [ApiTitle]
    )
    if type(TMDBAPIDataCache) != dict:
        TMDBAPIDataCache = {}
    for CacheKey in CandidateKeys:
        TMDBAPIDataCache[f'en:{CacheKey}'] = ApiTitle
        Auxiliary_SetPersistentCache('TMDB_EN', CacheKey, ApiTitle)
    Auxiliary_Log(f'{ApiTitle} << TMDBApi(EN)查询结果')
    return ApiTitle


def Auxiliary_OpenAITranslateForeignTitleToChinese(ForeignTitle):
    '''将外文剧名译为简体中文（剧名链最后一步）'''
    global USEOPENAIAPI
    ForeignTitle = Auxiliary_NormalizeDisplayTitle(ForeignTitle)
    if ForeignTitle in [None, '']:
        return None
    if USEOPENAIAPI != True:
        return None
    ApiKey = Auxiliary_GetOpenAIApiKey()
    if ApiKey in [None,'']:
        Auxiliary_Log('OpenAI 译名需要密钥','WARNING')
        return None
    BaseUrl = OPENAI_BASE_URL if OPENAI_BASE_URL not in [None,''] else 'https://api.longcat.chat/openai'
    ModelName = OPENAI_MODEL if OPENAI_MODEL not in [None,''] else 'LongCat-Flash-Chat'
    TimeoutSeconds = Auxiliary_ParseInt(OPENAI_TIMEOUT_SECONDS, 60)
    if TimeoutSeconds <= 0:
        TimeoutSeconds = 60
    try:
        HttpData = post(
            f'{BaseUrl.rstrip("/")}/v1/chat/completions',
            json={
                'model':ModelName,
                'temperature':0,
                'messages':[
                    {'role':'system','content':'你是番剧译名助手。输入为一部动画的日文/英文或罗马音标题，请只输出一个最常用的简体中文官方译名，不要季数、集数、引号或解释。无法确定则只输出空字符串。'},
                    {'role':'user','content':ForeignTitle}
                ]
            },
            headers={
                'Authorization':f'Bearer {ApiKey}',
                'Content-Type':'application/json',
                'User-Agent':f'AutoAnimeMv/{Versions}'
            },
            timeout=TimeoutSeconds
        )
        if HttpData.status_code != 200:
            Auxiliary_Log(f'OpenAI 译名请求失败,状态码 {HttpData.status_code}','WARNING')
            return None
        OpenAIData = HttpData.json()
        if type(OpenAIData) != dict:
            return None
        Choices = OpenAIData.get('choices', [])
        if type(Choices) != list or Choices == []:
            return None
        Message = Choices[0].get('message', {})
        RawText = Message.get('content', '') if type(Message) == dict else ''
        Parsed = Auxiliary_ParseJsonFromAIContent(RawText)
        if type(Parsed) == dict:
            ApiTitle = Auxiliary_NormalizeApiTitle(
                Parsed.get('anime_name_zh') or Parsed.get('anime_name') or Parsed.get('title') or ''
            )
        else:
            ApiTitle = Auxiliary_NormalizeApiTitle(RawText)
        if ApiTitle in ['', 'None', 'none', 'null', '未知', '无法识别', '无法判断', '不确定']:
            return None
        if Auxiliary_HasChineseText(ApiTitle) != True:
            return None
        return ApiTitle
    except Exception as err:
        Auxiliary_Log(f'OpenAI 译名失败: {err}','WARNING')
        return None


def Auxiliary_ResolvePlannedTitleChain(AINameZH, NameEN, NameRomaji, QueryFileName):
    '''
    剧名：TMDB 中文 → Bangumi 中文 → TMDB 英文 → OpenAI 译中文。
    返回 (中文主名, CanonicalID, NameEN, NameRomaji)；失败则 Auxiliary_Exit。
    '''
    global USETMDBAPI,USEBANGUMIAPI,USEOPENAIAPI
    AINameZH = Auxiliary_NormalizeApiTitle(AINameZH or '')
    NameEN = Auxiliary_NormalizeDisplayTitle(NameEN or '')
    NameRomaji = Auxiliary_NormalizeDisplayTitle(NameRomaji or '')
    BaseName = path.basename(str(QueryFileName))
    queries = []
    for q in [AINameZH, NameRomaji, NameEN, BaseName]:
        qn = Auxiliary_NormalizeDisplayTitle(q or '')
        if qn not in [None, ''] and qn not in queries:
            queries.append(qn)
    AliasBundle = queries.copy()

    if (ManualWhitelistedTitle := Auxiliary_GetManualWhitelistedTitle(*queries)) not in [None, '']:
        cid, zh = Auxiliary_UpsertCanonicalTitle(
            ManualWhitelistedTitle, NameEN, NameRomaji, 'manual', AliasBundle
        )
        return (zh if zh not in [None, ''] else ManualWhitelistedTitle), (cid or ''), NameEN, NameRomaji

    for q in queries:
        if USETMDBAPI == True:
            zh = Auxiliary_QueryTMDBChineseTitle(q, CandidateEn=NameEN or q, CandidateRomaji=NameRomaji, AliasList=AliasBundle)
            if zh not in [None, ''] and Auxiliary_HasChineseText(zh):
                cid, final = Auxiliary_UpsertCanonicalTitle(zh, NameEN, NameRomaji, 'TMDB', AliasBundle)
                return (final if final not in [None, ''] else zh), (cid or ''), NameEN, NameRomaji
    for q in queries:
        if USEBANGUMIAPI == True:
            zh = Auxiliary_QueryBangumiChineseTitle(q, CandidateEn=NameEN or q, CandidateRomaji=NameRomaji, AliasList=AliasBundle)
            if zh not in [None, ''] and Auxiliary_HasChineseText(zh):
                cid, final = Auxiliary_UpsertCanonicalTitle(zh, NameEN, NameRomaji, 'Bangumi', AliasBundle)
                return (final if final not in [None, ''] else zh), (cid or ''), NameEN, NameRomaji

    EnTitle = None
    for q in queries:
        if USETMDBAPI == True:
            EnTitle = Auxiliary_QueryTMDBEnglishTitle(q, CandidateEn=NameEN or q, CandidateRomaji=NameRomaji, AliasList=AliasBundle)
            if EnTitle not in [None, '']:
                if NameEN in [None, '']:
                    NameEN = EnTitle
                break
    ForeignForTranslate = EnTitle or NameEN or NameRomaji or ''
    if ForeignForTranslate in [None, ''] and queries:
        ForeignForTranslate = queries[0]
    if USEOPENAIAPI == True:
        Translated = Auxiliary_OpenAITranslateForeignTitleToChinese(ForeignForTranslate)
        if Translated not in [None, '']:
            cid, final = Auxiliary_UpsertCanonicalTitle(Translated, NameEN or ForeignForTranslate, NameRomaji, 'OpenAI', AliasBundle)
            return (final if final not in [None, ''] else Translated), (cid or ''), NameEN, NameRomaji
    if AINameZH not in [None, ''] and Auxiliary_HasChineseText(AINameZH):
        cid, final = Auxiliary_UpsertCanonicalTitle(AINameZH, NameEN, NameRomaji, 'OpenAI', AliasBundle)
        return (final if final not in [None, ''] else AINameZH), (cid or ''), NameEN, NameRomaji
    Auxiliary_Exit('剧名解析链失败：TMDB 中文、Bangumi、TMDB 英文与 OpenAI 译中文均未得到可用简体中文剧名，已中止整理')


def Auxiliary_FormatOrganizedEpisodeTag(SE, EP):
    SEValue = Auxiliary_FormatSEEPToken(SE)
    EPValue = Auxiliary_FormatSEEPToken(EP)
    return f'S{SEValue}E{EPValue}'


def Auxiliary_GetShowOrganizationRecord(CanonicalID):
    global ShowOrganizationIndexDataCache
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return None
    if CanonicalID in ShowOrganizationIndexDataCache:
        return ShowOrganizationIndexDataCache[CanonicalID]
    Raw = Auxiliary_GetPersistentCache('ShowOrganizationIndex', CanonicalID)
    if type(Raw) != dict:
        return None
    ShowOrganizationIndexDataCache[CanonicalID] = Raw
    return Raw


def Auxiliary_OrderedShowRecordDict(Record):
    if type(Record) != dict:
        Record = {}
    return {
        'canonical_id': str(Record.get('canonical_id', '')),
        'organized_episodes': list(Record.get('organized_episodes', [])) if type(Record.get('organized_episodes')) == list else [],
        'title_en': str(Record.get('title_en', '')),
        'title_romaji': str(Record.get('title_romaji', '')),
        'title_zh': str(Record.get('title_zh', '')),
        'v': int(Record.get('v', 1)),
    }


def Auxiliary_SetShowOrganizationRecord(CanonicalID, Record):
    global ShowOrganizationIndexDataCache,PersistentApiCacheDirty
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return
    Record = Record.copy()
    Record['canonical_id'] = CanonicalID
    if 'organized_episodes' not in Record or type(Record['organized_episodes']) != list:
        Record['organized_episodes'] = []
    Record['v'] = int(Record.get('v', 1))
    Ordered = Auxiliary_OrderedShowRecordDict(Record)
    ShowOrganizationIndexDataCache[CanonicalID] = Ordered
    Auxiliary_SetPersistentCache('ShowOrganizationIndex', CanonicalID, Ordered)


def Auxiliary_ShowHasOrganizedEpisode(CanonicalID, SE, EP):
    Rec = Auxiliary_GetShowOrganizationRecord(CanonicalID)
    if type(Rec) != dict:
        return False
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    EpList = Rec.get('organized_episodes', [])
    if type(EpList) != list:
        return False
    return Tag in EpList


def Auxiliary_ShowMarkOrganizedEpisode(CanonicalID, title_zh, title_en, title_romaji, SE, EP):
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
            'v': 1
        }
    EpList = list(Rec.get('organized_episodes', [])) if type(Rec.get('organized_episodes')) == list else []
    Tag = Auxiliary_FormatOrganizedEpisodeTag(SE, EP)
    if Tag not in EpList:
        EpList.append(Tag)
    Rec['organized_episodes'] = sorted(EpList)
    Rec['title_zh'] = Auxiliary_NormalizeApiTitle(title_zh or Rec.get('title_zh', ''))
    Rec['title_en'] = Auxiliary_NormalizeDisplayTitle(title_en or Rec.get('title_en', ''))
    Rec['title_romaji'] = Auxiliary_NormalizeDisplayTitle(title_romaji or Rec.get('title_romaji', ''))
    Auxiliary_SetShowOrganizationRecord(CanonicalID, Rec)


def Auxiliary_IDE_ParseSeasonTokensFromFile(File):
    '''仅从文件名解析季号，不截断剧名。返回 (SE, RAWSE, RomanSeasonToken)'''
    SeasonMatchData = r'(季(.*?)第)|(([0-9]{0,1}[0-9]{1})S)|(([0-9]{0,1}[0-9]{1})nosaeS)|(([0-9]{0,1}[0-9]{1}) nosaeS)|(([0-9]{0,1}[0-9]{1})-nosaeS)|(nosaeS-dn([0-9]{1}))|(nosaeS-dr([0-9]{1}))'
    SE = None
    RAWSE = ''
    RomanToken = ''
    if (X := findall(SeasonMatchData,File[::-1],flags=I)) != []:
        SEData = X
        SEList = []
        for sedata in SEData:
            for se in sedata:
                if se != '' and se.isnumeric() == False:
                    RomanToken = se[::-1]
                elif se.isnumeric() == True:
                    SEList.append(se)
        for i in range(len(SEList)):
            if SEList[i].isdecimal() == True:
                SE = SEList[i][::-1]
            elif '\u0e00' <= SEList[i] <= '\u9fa5':
                digit = {'一':'01', '二':'02', '三':'03', '四':'04', '五':'05', '六':'06', '七':'07', '八':'08', '九':'09','壹':'01','贰':'02','叁':'03','肆':'04','伍':'05','陆':'06','柒':'07','捌':'08','玖':'09'}
                SE = digit.get(SEList[i], '01')
            if SE is not None:
                RAWSE = str(SE).lstrip('0') or str(SE)
                SE = str(SE)
                return SE, RAWSE, RomanToken
    elif (X := findall(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]',File[::-1],flags=I)) != []:
        A = {'Ⅰ':'01','Ⅱ':'02','Ⅲ':'03','Ⅳ':'04','Ⅴ':'05','Ⅵ':'06','Ⅶ':'07','Ⅷ':'08','Ⅸ':'09','Ⅹ':'10','Ⅺ':'11','Ⅻ':'12'}
        SE = A[X[0]]
        return SE, str(int(SE)), X[0]
    return '01', '1', ''


def Auxiliary_ParseTMDBTvDetailsSeasonLayout(DetailsData):
    '''从 TMDB tv/{id} 详情中解析正片分季集数列表，忽略第 0 季特典。'''
    if type(DetailsData) != dict:
        return []
    RawSeasons = DetailsData.get('seasons', [])
    if type(RawSeasons) != list:
        return []
    Pairs = []
    for Item in RawSeasons:
        if type(Item) != dict:
            continue
        try:
            Sn = int(Item.get('season_number', -1))
            Ec = int(Item.get('episode_count', 0))
        except (TypeError, ValueError):
            continue
        if Sn < 1 or Ec < 1:
            continue
        Pairs.append((Sn, Ec))
    Pairs.sort(key=lambda X: X[0])
    return Pairs


def Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(AbsEp, SeasonPairs):
    '''
    将「全剧累计集号」映射到 (季号, 该季内的集号)。
    最后一季若累计集超出 TMDB 已登记的 episode_count，仍归入最后一季并顺延集号（应对新番未更新完）。
    '''
    if AbsEp < 1 or type(SeasonPairs) != list or SeasonPairs == []:
        return None
    Prefix = 0
    LastIndex = len(SeasonPairs) - 1
    for Idx, (SeasonNum, EpCount) in enumerate(SeasonPairs):
        if Idx == LastIndex:
            return SeasonNum, AbsEp - Prefix
        if AbsEp <= Prefix + EpCount:
            return SeasonNum, AbsEp - Prefix
        Prefix += EpCount
    return None


def Auxiliary_GetTMDBTvSeasonLayoutBySeriesId(tv_id):
    global TMDBTvSeasonLayoutMemoryCache
    try:
        TvIdInt = int(tv_id)
    except (TypeError, ValueError):
        return []
    if TvIdInt in TMDBTvSeasonLayoutMemoryCache:
        return TMDBTvSeasonLayoutMemoryCache[TvIdInt]
    CachedRaw = Auxiliary_GetPersistentCache('TMDBTvSeasons', f'id:{TvIdInt}')
    if type(CachedRaw) == list and CachedRaw != []:
        Pairs = []
        for Row in CachedRaw:
            if type(Row) in (list, tuple) and len(Row) >= 2:
                try:
                    Pairs.append((int(Row[0]), int(Row[1])))
                except (TypeError, ValueError):
                    continue
        if Pairs != []:
            TMDBTvSeasonLayoutMemoryCache[TvIdInt] = Pairs
            return Pairs
    if USETMDBAPI != True or Auxiliary_GetTMDBBearerToken() in [None, '']:
        return []
    Details = Auxiliary_Http(
        f'https://api.themoviedb.org/3/tv/{TvIdInt}',
        ResponseType='json',
        Timeout=25,
    )
    Pairs = Auxiliary_ParseTMDBTvDetailsSeasonLayout(Details)
    if Pairs != []:
        TMDBTvSeasonLayoutMemoryCache[TvIdInt] = Pairs
        Auxiliary_SetPersistentCache(
            'TMDBTvSeasons',
            f'id:{TvIdInt}',
            [[Sn, Ec] for Sn, Ec in Pairs],
        )
    return Pairs


def Auxiliary_ResolveTMDBTvSeriesIdFromEnglishQuery(QueryName):
    global TMDBTvSeriesIdMemoryCache
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    if QueryName in [None, '']:
        return None
    AliasKey = Auxiliary_NormalizeAliasKey(QueryName)
    if AliasKey in [None, '']:
        return None
    if AliasKey in TMDBTvSeriesIdMemoryCache:
        return TMDBTvSeriesIdMemoryCache[AliasKey]
    CachedId = Auxiliary_GetPersistentCache('TMDBTvSeriesId', AliasKey)
    try:
        CachedId = int(CachedId)
    except (TypeError, ValueError):
        CachedId = 0
    if CachedId > 0:
        TMDBTvSeriesIdMemoryCache[AliasKey] = CachedId
        return CachedId
    if USETMDBAPI != True or Auxiliary_GetTMDBBearerToken() in [None, '']:
        return None
    SearchData = Auxiliary_Http(
        f'https://api.themoviedb.org/3/search/tv?query={quote(QueryName)}&include_adult=false&language=en-US&page=1',
        ResponseType='json',
        Timeout=20,
    )
    if type(SearchData) != dict:
        return None
    ResultList = SearchData.get('results', [])
    if type(ResultList) != list or ResultList == [] or type(ResultList[0]) != dict:
        return None
    Tid = ResultList[0].get('id')
    try:
        Tid = int(Tid)
    except (TypeError, ValueError):
        return None
    if Tid < 1:
        return None
    TMDBTvSeriesIdMemoryCache[AliasKey] = Tid
    Auxiliary_SetPersistentCache('TMDBTvSeriesId', AliasKey, Tid)
    return Tid


def Auxiliary_ResolveTMDBTvIdForJujutsuKaisen(NameEN, NameRomaji):
    QueryList = []
    for Q in (NameEN, NameRomaji, 'Jujutsu Kaisen'):
        Qn = Auxiliary_NormalizeDisplayTitle(Q or '')
        if Qn != '' and Qn not in QueryList:
            QueryList.append(Qn)
    for Qn in QueryList:
        Tid = Auxiliary_ResolveTMDBTvSeriesIdFromEnglishQuery(Qn)
        if Tid not in [None, ''] and int(Tid) > 0:
            return int(Tid)
    return None


def Auxiliary_QueryBangumiChineseTitle(QueryName, CandidateEn='', CandidateRomaji='', AliasList=None):
    '''仅通过 Bangumi 查询中文标题；未命中中文时返回 None'''
    global USEBANGUMIAPI,BangumiAPIDataCache
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    CandidateEn = Auxiliary_NormalizeDisplayTitle(CandidateEn)
    CandidateRomaji = Auxiliary_NormalizeDisplayTitle(CandidateRomaji)
    if QueryName in [None, ''] or USEBANGUMIAPI != True:
        return None

    CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(
        QueryName,
        CandidateEn,
        CandidateRomaji
    )
    if CanonicalZh not in [None, '']:
        return CanonicalZh

    CandidateKeys = Auxiliary_GetStandardTitleCacheCandidates(QueryName)
    if QueryName not in CandidateKeys:
        CandidateKeys.insert(0, QueryName)
    for CacheKey in CandidateKeys:
        CacheValue = None
        if type(BangumiAPIDataCache) == dict and CacheKey in BangumiAPIDataCache:
            CacheValue = BangumiAPIDataCache.get(CacheKey)
            Auxiliary_Log(f'{CacheValue} << Bangumi内存缓存查询结果')
        else:
            CacheValue = Auxiliary_GetPersistentCache('Bangumi', CacheKey)
            if CacheValue not in [None, '']:
                if type(BangumiAPIDataCache) != dict:
                    BangumiAPIDataCache = {}
                BangumiAPIDataCache[CacheKey] = CacheValue
                Auxiliary_Log(f'{CacheValue} << Bangumi持久化缓存查询结果')
        CacheValue = Auxiliary_NormalizeApiTitle(CacheValue)
        if CacheValue in [None, ''] or Auxiliary_HasChineseText(CacheValue) == False:
            continue
        return CacheValue

    BangumiApiData = Auxiliary_Http(
        f"https://api.bgm.tv/search/subject/{quote(QueryName)}?type=2&responseGroup=medium&max_results=1",
        ResponseType='json',
        Timeout=20
    )
    if type(BangumiApiData) != dict:
        Auxiliary_Log(f'BangumiApi查询失败: {QueryName}','WARNING')
        return None
    ResultList = BangumiApiData.get('list', [])
    if type(ResultList) != list or ResultList == [] or type(ResultList[0]) != dict:
        Auxiliary_Log(f'BangumiApi没有检索到关于 {QueryName} 内容','WARNING')
        return None

    AnimeData = ResultList[0]
    ApiTitle = Auxiliary_NormalizeApiTitle(AnimeData.get('name_cn') or AnimeData.get('name') or '')
    if ApiTitle in [None, ''] or Auxiliary_HasChineseText(ApiTitle) == False:
        Auxiliary_Log(f'BangumiApi未返回可用中文标题: {QueryName}','WARNING')
        return None

    CandidateEnForUpsert = CandidateEn
    if CandidateEnForUpsert in [None, ''] and Auxiliary_HasChineseText(QueryName) == False:
        CandidateEnForUpsert = QueryName
    CandidateAliases = [QueryName, CandidateEn, CandidateRomaji]
    if type(AliasList) == list:
        CandidateAliases.extend(AliasList)
    CandidateAliases = [Auxiliary_NormalizeDisplayTitle(Item) for Item in CandidateAliases if Item not in [None, '']]

    _, CanonicalTitle = Auxiliary_UpsertCanonicalTitle(
        ApiTitle,
        CandidateEnForUpsert,
        CandidateRomaji,
        'Bangumi',
        CandidateAliases
    )
    if CanonicalTitle not in [None, ''] and Auxiliary_HasChineseText(CanonicalTitle):
        ApiTitle = CanonicalTitle
    for CacheKey in CandidateKeys:
        BangumiAPIDataCache[CacheKey] = ApiTitle
        Auxiliary_SetPersistentCache('Bangumi', CacheKey, ApiTitle)
    Auxiliary_Log(f'{ApiTitle} << BangumiApi查询结果')
    return ApiTitle


def Auxiliary_ParseJsonFromAIContent(Text):
    Text = '' if Text in [None, ''] else str(Text).strip()
    if Text == '':
        return None
    Text = sub(r'^```[a-zA-Z0-9_-]*\s*','',Text)
    Text = sub(r'\s*```$','',Text)
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
    T = sub(r'(?<=[\u4e00-\u9fff])\(', '（', T)
    T = sub(r'\((?=[\u4e00-\u9fff])', '（', T)
    T = sub(r'(?<=[\u4e00-\u9fff])\)', '）', T)
    T = sub(r'\)(?=[\u4e00-\u9fff])', '）', T)
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
    Title = sub(r'\s+',' ',Title).strip()
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
    Title = sub(r'第\s*[0-9]{1,3}\s*季','',Title,flags=I)
    Title = sub(r'[0-9]{1,3}(st|nd|rd|th)\s*season','',Title,flags=I)
    Title = sub(r'season\s*[0-9]{1,3}','',Title,flags=I)
    Title = sub(r'(^|[^a-z0-9])s\s*[0-9]{1,3}([^a-z0-9]|$)',' ',Title,flags=I)
    Title = sub(r'[\[\]【】\(\)（）]',' ',Title)
    Title = sub(r'[\-_/\\:：\.,，。!！?？~～]+',' ',Title)
    Title = sub(r'\s+','',Title)
    Title = sub(r'[^0-9a-z\u4e00-\u9fff]+','',Title)
    return Title


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


def Auxiliary_GetTitleSourcePriority(SourceTag):
    SourceTag = '' if SourceTag in [None, ''] else str(SourceTag)
    PriorityMap = {
        'manual': 100,
        'Bangumi': 95,
        'BGM': 90,
        'TMDB': 80,
        'openai_identify': 75,
        'OpenAI': 70,
        'legacy': 50,
        'unknown': 40
    }
    return PriorityMap.get(SourceTag, 45)


def Auxiliary_ShouldPreferChineseTitle(OldTitle, NewTitle, OldSource='unknown', NewSource='unknown'):
    NewTitle = Auxiliary_NormalizeDisplayTitle(NewTitle)
    OldTitle = Auxiliary_NormalizeDisplayTitle(OldTitle)
    if NewTitle == '':
        return False
    if NewTitle in ['未知','无法识别','无法判断','不确定']:
        return False
    OldHasChinese = Auxiliary_HasChineseText(OldTitle)
    NewHasChinese = Auxiliary_HasChineseText(NewTitle)
    if NewHasChinese == False:
        return False
    if OldTitle == '':
        return True
    if NewHasChinese and OldHasChinese == False:
        return True
    if NewHasChinese == False and OldHasChinese:
        return False
    if OldTitle in ['未知','无法识别','无法判断','不确定']:
        return True
    NewPriority = Auxiliary_GetTitleSourcePriority(NewSource)
    OldPriority = Auxiliary_GetTitleSourcePriority(OldSource)
    if NewPriority >= OldPriority + 5 and NewTitle != OldTitle:
        return True
    if NewHasChinese and OldHasChinese and len(NewTitle) >= len(OldTitle) + 3:
        return True
    if Auxiliary_ShouldPreferShorterJujutsuMainTitle(OldTitle, NewTitle):
        return True
    return False


def Auxiliary_ShouldPreferShorterJujutsuMainTitle(OldTitle, NewTitle):
    OldTitle = Auxiliary_NormalizeDisplayTitle(OldTitle)
    NewTitle = Auxiliary_NormalizeDisplayTitle(NewTitle)
    if NewTitle != '咒术回战':
        return False
    if Auxiliary_HasChineseText(OldTitle) == False or ('咒术' in OldTitle and '回战' in OldTitle) == False:
        return False
    if any(Fragment in OldTitle for Fragment in ('怀玉', '玉折', '涩谷', '渋谷', '死灭')):
        return True
    return False


def Auxiliary_IsJujutsuKaisenSeries(NameEN='', NameRomaji='', NameZH=''):
    Key = Auxiliary_NormalizeAliasKey(NameEN or NameRomaji or '')
    if Key == 'jujutsukaisen':
        return True
    Zh = NameZH or ''
    if Auxiliary_HasChineseText(Zh) and '咒术' in Zh and '回战' in Zh:
        return True
    return False


def Auxiliary_ContractJujutsuKaisenChineseTitle(ChineseTitle):
    ChineseTitle = Auxiliary_NormalizeApiTitle(ChineseTitle)
    if ChineseTitle in [None, ''] or Auxiliary_HasChineseText(ChineseTitle) == False:
        return ChineseTitle
    if ('咒术' in ChineseTitle and '回战' in ChineseTitle) == False:
        return ChineseTitle
    if any(Fragment in ChineseTitle for Fragment in ('怀玉', '玉折', '涩谷', '渋谷', '死灭')):
        return '咒术回战'
    return ChineseTitle


def Auxiliary_RemappedJujutsuKaisenSeasonEpisode(RAWSE, RAWEP, SE, EP, NameEN, NameRomaji, NameZH):
    global SEEPSINGLECHARACTER
    if Auxiliary_IsJujutsuKaisenSeries(NameEN, NameRomaji, NameZH) == False:
        return None
    RAWEP = str(RAWEP or '').strip()
    if RAWEP == '' or RAWEP.split('.')[0].isdigit() == False:
        return None
    AbsEp = int(RAWEP.split('.')[0])
    SeasonPairs = []
    TvId = Auxiliary_ResolveTMDBTvIdForJujutsuKaisen(NameEN, NameRomaji)
    if TvId not in [None, '']:
        SeasonPairs = Auxiliary_GetTMDBTvSeasonLayoutBySeriesId(TvId)
    FirstSeasonCap = SeasonPairs[0][1] if SeasonPairs else 24
    if AbsEp <= FirstSeasonCap:
        return None
    Mapped = Auxiliary_MapAbsoluteEpisodeUsingTMDBSeasonLayout(AbsEp, SeasonPairs) if SeasonPairs else None
    if Mapped == None:
        if AbsEp <= 47:
            NewRAWSE = '2'
            NewRAWEP = str(AbsEp - 24)
        else:
            NewRAWSE = '3'
            NewRAWEP = str(AbsEp - 47)
    else:
        NewSeasonNum, NewEpInSeason = Mapped
        NewRAWSE = str(int(NewSeasonNum))
        NewRAWEP = str(int(NewEpInSeason))
    NewSE = NewRAWSE.zfill(2) if SEEPSINGLECHARACTER == False else NewRAWSE.lstrip('0')
    if NewSE in [None, '']:
        NewSE = '1' if SEEPSINGLECHARACTER == True else '01'
    NewEP = '0' + NewRAWEP if (len(NewRAWEP) < 2 or ('.' in NewRAWEP and NewRAWEP[0] != '0')) and (SEEPSINGLECHARACTER == False) else NewRAWEP
    if SEEPSINGLECHARACTER == True:
        NewSE = NewSE.lstrip('0')
        NewEP = NewEP.lstrip('0')
        NewSE = NewSE if NewSE not in [None, ''] else '0'
        NewEP = NewEP if NewEP not in [None, ''] else '0'
    return NewRAWSE, NewRAWEP, NewSE, NewEP


def Auxiliary_NormalizeEpisodeToken(RawEpisode, FileName=''):
    RawEpisode = '' if RawEpisode in [None, ''] else str(RawEpisode).strip()
    if RawEpisode == '':
        return '', True
    RawEpisode = RawEpisode.replace('．', '.')
    DecimalMatch = match(r'^([0-9]{1,4})\.([0-9]{1,2})$', RawEpisode)
    if DecimalMatch != None:
        IntPart = DecimalMatch.group(1)
        DecimalPart = DecimalMatch.group(2)
        if DecimalPart.strip('0') == '':
            RawEpisode = str(int(IntPart))
        elif DecimalPart == '5' and search(r'(?i)v[2-9]', str(FileName)) != None:
            # 例如 02v2，避免被错误解释成 2.5 特典
            RawEpisode = str(int(IntPart))
    IsSpecial = (RawEpisode in ['0', '00']) or ('.' in RawEpisode)
    return RawEpisode, IsSpecial


def Auxiliary_CoalesceEpisodeFromParsed(ParsedData):
    '''从模型 JSON 取剧集字段；不能用 `or` 链（episode 为整数 0 时会被当成假值丢弃）'''
    if type(ParsedData) != dict:
        return ''
    for Key in ('episode', 'ep'):
        if Key not in ParsedData:
            continue
        Val = ParsedData[Key]
        if Val is None:
            continue
        Raw = str(Val).strip()
        if Raw != '':
            return Raw
    return ''


def Auxiliary_CoalesceSeasonFromParsed(ParsedData, DefaultSeason='1'):
    if type(ParsedData) != dict:
        return DefaultSeason
    for Key in ('season', 'se'):
        if Key not in ParsedData:
            continue
        Val = ParsedData[Key]
        if Val is None:
            continue
        Raw = str(Val).strip()
        if Raw != '':
            return Raw
    return DefaultSeason


def Auxiliary_NoteOpenAIIdentifyFailure(reason, detail='', **extra):
    global LastOpenAIIdentifyFailure
    Pack = {'reason': str(reason), 'detail': str(detail)}
    for Key, Val in extra.items():
        Pack[Key] = Val
    LastOpenAIIdentifyFailure = Pack


def Auxiliary_GetOpenAIIdentifyWarningLogPath():
    if 'Runtime' in globals() and Runtime and getattr(Runtime, 'source_path', None):
        LogBasePath = Runtime.source_path
        if LogBasePath.exists() == False:
            LogBasePath = PathlibPath(PyPath)
    else:
        LogBasePath = PathlibPath(PyPath if 'PyPath' in globals() else '.')
    OpDirName = str(OPERATION_LOG_DIR).strip() if 'OPERATION_LOG_DIR' in globals() and OPERATION_LOG_DIR not in [None, ''] else 'logs'
    return LogBasePath / OpDirName / 'AutoAnime_openai_identify_warnings.json'


def Auxiliary_AppendOpenAIIdentifyWarningLog(entry: dict):
    '''追加 OpenAI 全信息识别失败记录到 logs/AutoAnime_openai_identify_warnings.json，records 按 timestamp 排序'''
    LogPath = Auxiliary_GetOpenAIIdentifyWarningLogPath()
    try:
        LogPath.parent.mkdir(parents=True, exist_ok=True)
        Records = []
        if LogPath.is_file():
            with open(LogPath, 'r', encoding='UTF-8') as LogFile:
                try:
                    OldPayload = json.load(LogFile)
                    if type(OldPayload) == dict and type(OldPayload.get('records')) == list:
                        Records = OldPayload['records']
                except Exception:
                    Records = []
        Row = dict(entry) if type(entry) == dict else {'detail': str(entry)}
        if 'timestamp' not in Row:
            Row['timestamp'] = strftime('%Y-%m-%d %H:%M:%S', localtime(time()))
        if 'run_id' not in Row and 'CurrentRunID' in globals():
            Row['run_id'] = CurrentRunID
        Records.append(Row)
        Records.sort(key=lambda r: (str(r.get('timestamp', '')), str(r.get('run_id', '')), str(r.get('input_basename', ''))))
        with open(LogPath, 'w', encoding='UTF-8') as LogFile:
            json.dump({'records': Records}, LogFile, ensure_ascii=False, indent=2)
    except Exception as err:
        Auxiliary_Log(f'OpenAI 识别告警日志写入失败: {err}', 'WARNING')


def Auxiliary_RemoveEpisodeSuffixFromTitle(Title, RawEpisode):
    Title = Auxiliary_NormalizeDisplayTitle(Title)
    EpisodeValue, _ = Auxiliary_NormalizeEpisodeToken(RawEpisode)
    if Title == '' or EpisodeValue == '' or EpisodeValue.isdigit() == False:
        return Auxiliary_NormalizeApiTitle(Title)
    EpisodeInt = str(int(EpisodeValue))
    CandidateTitle = Title
    CandidateTitle = sub(rf'[\s\-_]+0*{EpisodeInt}$', '', CandidateTitle, flags=I).strip(' -_')
    CandidateTitle = sub(rf'第\s*0*{EpisodeInt}\s*[话話集]$', '', CandidateTitle, flags=I).strip(' -_')
    CandidateTitle = sub(rf'[\(\[（【]\s*0*{EpisodeInt}\s*[\)\]）】]$', '', CandidateTitle, flags=I).strip(' -_')
    if CandidateTitle not in [None, '']:
        return Auxiliary_NormalizeApiTitle(CandidateTitle)
    return Auxiliary_NormalizeApiTitle(Title)


def Auxiliary_GetAliasCanonicalID(AliasTitle):
    global TitleAliasIndexDataCache
    AliasKey = Auxiliary_NormalizeAliasKey(AliasTitle)
    if AliasKey == '':
        return None
    if AliasKey in TitleAliasIndexDataCache:
        return TitleAliasIndexDataCache[AliasKey]
    CanonicalID = Auxiliary_GetPersistentCache('TitleAliasIndex', AliasKey)
    if CanonicalID not in [None, '']:
        TitleAliasIndexDataCache[AliasKey] = CanonicalID
        return CanonicalID
    return None


def Auxiliary_GetCanonicalTitleRecord(CanonicalID):
    global CanonicalTitleIndexDataCache
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if CanonicalID == '':
        return None
    Record = None
    if CanonicalID in CanonicalTitleIndexDataCache:
        Record = CanonicalTitleIndexDataCache.get(CanonicalID)
    else:
        Record = Auxiliary_GetPersistentCache('CanonicalTitleIndex', CanonicalID)
        if Record not in [None, '']:
            CanonicalTitleIndexDataCache[CanonicalID] = Record
    if type(Record) != dict:
        return None
    FixedRecord = {
        'zh': Auxiliary_NormalizeDisplayTitle(Record.get('zh', '')),
        'en': Auxiliary_NormalizeDisplayTitle(Record.get('en', '')),
        'romaji': Auxiliary_NormalizeDisplayTitle(Record.get('romaji', '')),
        'source': str(Record.get('source', 'unknown')),
        'last_updated': str(Record.get('last_updated', '')),
        'confidence': Auxiliary_ParseInt(Record.get('confidence', 0), 0),
    }
    return FixedRecord


def Auxiliary_LinkAliasToCanonical(AliasTitle, CanonicalID):
    global TitleAliasIndexDataCache
    AliasKey = Auxiliary_NormalizeAliasKey(AliasTitle)
    CanonicalID = '' if CanonicalID in [None, ''] else str(CanonicalID)
    if AliasKey == '' or CanonicalID == '':
        return
    if TitleAliasIndexDataCache.get(AliasKey) == CanonicalID:
        return
    TitleAliasIndexDataCache[AliasKey] = CanonicalID
    Auxiliary_SetPersistentCache('TitleAliasIndex', AliasKey, CanonicalID)


def Auxiliary_ResolveCanonicalTitleByAliases(*AliasTitleList):
    CheckedAliasSet = set()
    FallbackCanonicalID = None
    FallbackCanonicalRecord = None
    for AliasTitle in AliasTitleList:
        AliasKey = Auxiliary_NormalizeAliasKey(AliasTitle)
        if AliasKey == '' or AliasKey in CheckedAliasSet:
            continue
        CheckedAliasSet.add(AliasKey)
        CanonicalID = Auxiliary_GetAliasCanonicalID(AliasTitle)
        if CanonicalID in [None, '']:
            continue
        CanonicalRecord = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
        if type(CanonicalRecord) != dict:
            continue
        if FallbackCanonicalID in [None, '']:
            FallbackCanonicalID = CanonicalID
            FallbackCanonicalRecord = CanonicalRecord
        CanonicalZh = Auxiliary_NormalizeApiTitle(CanonicalRecord.get('zh', ''))
        if CanonicalZh not in [None, '']:
            return CanonicalZh, CanonicalID, CanonicalRecord
    if FallbackCanonicalID not in [None, '']:
        return None, FallbackCanonicalID, FallbackCanonicalRecord
    return None, None, None


def Auxiliary_UpsertCanonicalTitle(ChineseTitle='', EnglishTitle='', RomajiTitle='', SourceTag='unknown', AliasList=None):
    global CanonicalTitleIndexDataCache
    ChineseTitle = Auxiliary_NormalizeApiTitle(ChineseTitle)
    EnglishTitle = Auxiliary_NormalizeDisplayTitle(EnglishTitle)
    RomajiTitle = Auxiliary_NormalizeDisplayTitle(RomajiTitle)
    AllAliases = [ChineseTitle, EnglishTitle, RomajiTitle]
    if type(AliasList) in [list, tuple]:
        for OneAlias in AliasList:
            if OneAlias not in [None, '']:
                AllAliases.append(Auxiliary_NormalizeDisplayTitle(OneAlias))
    CandidateCanonicalIDs = []
    for AliasTitle in AllAliases:
        if (MatchedCanonicalID := Auxiliary_GetAliasCanonicalID(AliasTitle)) not in [None, '']:
            if MatchedCanonicalID not in CandidateCanonicalIDs:
                CandidateCanonicalIDs.append(MatchedCanonicalID)
    if CandidateCanonicalIDs == []:
        SeedTitle = ChineseTitle if ChineseTitle not in [None, ''] else (EnglishTitle if EnglishTitle not in [None, ''] else RomajiTitle)
        CanonicalID = Auxiliary_NormalizeAliasKey(SeedTitle)
        if CanonicalID in [None, '']:
            return None, ChineseTitle
    else:
        CanonicalID = CandidateCanonicalIDs[0]
        BestRecord = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
        for OneCanonicalID in CandidateCanonicalIDs[1:]:
            OneRecord = Auxiliary_GetCanonicalTitleRecord(OneCanonicalID)
            if type(OneRecord) == dict and type(BestRecord) == dict:
                if Auxiliary_HasChineseText(OneRecord.get('zh', '')) and Auxiliary_HasChineseText(BestRecord.get('zh', '')) == False:
                    CanonicalID = OneCanonicalID
                    BestRecord = OneRecord
    ExistingRecord = Auxiliary_GetCanonicalTitleRecord(CanonicalID)
    if type(ExistingRecord) != dict:
        CanonicalRecord = {
            'zh': '',
            'en': '',
            'romaji': '',
            'source': 'unknown',
            'last_updated': '',
            'confidence': 0
        }
        ChangedFlag = True
    else:
        CanonicalRecord = ExistingRecord.copy()
        ChangedFlag = False
    if Auxiliary_ShouldPreferChineseTitle(CanonicalRecord.get('zh', ''), ChineseTitle, CanonicalRecord.get('source', 'unknown'), SourceTag):
        CanonicalRecord['zh'] = ChineseTitle
        CanonicalRecord['source'] = SourceTag
        ChangedFlag = True
    elif CanonicalRecord.get('source', '') in [None, '']:
        CanonicalRecord['source'] = SourceTag
        ChangedFlag = True
    if EnglishTitle not in [None, ''] and CanonicalRecord.get('en', '') in [None, '']:
        CanonicalRecord['en'] = EnglishTitle
        ChangedFlag = True
    if RomajiTitle not in [None, ''] and CanonicalRecord.get('romaji', '') in [None, '']:
        CanonicalRecord['romaji'] = RomajiTitle
        ChangedFlag = True
    NewConfidence = max(
        Auxiliary_ParseInt(CanonicalRecord.get('confidence', 0), 0),
        Auxiliary_GetTitleSourcePriority(SourceTag)
    )
    if NewConfidence != Auxiliary_ParseInt(CanonicalRecord.get('confidence', 0), 0):
        CanonicalRecord['confidence'] = NewConfidence
        ChangedFlag = True
    if ChangedFlag:
        CanonicalRecord['last_updated'] = strftime("%Y-%m-%d %H:%M:%S",localtime(time()))
    CanonicalTitleIndexDataCache[CanonicalID] = CanonicalRecord
    if ChangedFlag:
        Auxiliary_SetPersistentCache('CanonicalTitleIndex', CanonicalID, CanonicalRecord)
    for OneAlias in AllAliases + [CanonicalRecord.get('zh', ''), CanonicalRecord.get('en', ''), CanonicalRecord.get('romaji', '')]:
        Auxiliary_LinkAliasToCanonical(OneAlias, CanonicalID)
    return CanonicalID, CanonicalRecord.get('zh', '')


def Auxiliary_RebuildCanonicalIndexesFromPersistentCache():
    global PersistentApiCacheDirty
    if type(PersistentApiCache) != dict:
        return

    def IterateRawGroupValue(CacheGroup):
        GroupData = PersistentApiCache.get(CacheGroup, {})
        if type(GroupData) != dict:
            return []
        ReturnList = []
        for CacheKey, CacheRecord in GroupData.items():
            if type(CacheRecord) == dict and 'value' in CacheRecord:
                ReturnList.append((CacheKey, CacheRecord.get('value')))
        return ReturnList

    ChangedFlag = False
    for CacheGroup in ['Bangumi','TMDB']:
        for QueryName, CacheValue in IterateRawGroupValue(CacheGroup):
            if CacheValue in [None, '']:
                continue
            CandidateZh = Auxiliary_NormalizeApiTitle(CacheValue)
            CandidateEn = Auxiliary_NormalizeDisplayTitle(QueryName if QueryName not in [None, ''] else '')
            if Auxiliary_HasChineseText(CandidateZh) == False:
                if CandidateEn in [None, '']:
                    CandidateEn = Auxiliary_NormalizeDisplayTitle(CacheValue)
                CandidateZh = ''
            CanonicalID, CanonicalZh = Auxiliary_UpsertCanonicalTitle(CandidateZh, CandidateEn, '', CacheGroup, [QueryName, CacheValue])
            if CanonicalID not in [None, '']:
                if CanonicalZh not in [None, ''] and Auxiliary_HasChineseText(CanonicalZh):
                    if type(PersistentApiCache.get(CacheGroup, {}).get(QueryName)) == dict:
                        if PersistentApiCache[CacheGroup][QueryName].get('value') != CanonicalZh:
                            PersistentApiCache[CacheGroup][QueryName]['value'] = CanonicalZh
                            ChangedFlag = True

    for QueryName, CacheValue in IterateRawGroupValue('TMDB_EN'):
        if CacheValue in [None, '']:
            continue
        EnTitle = Auxiliary_NormalizeDisplayTitle(str(CacheValue))
        if EnTitle in [None, '']:
            continue
        Auxiliary_UpsertCanonicalTitle('', EnTitle, '', 'TMDB', [QueryName, EnTitle])

    for CanonicalKey, CacheValue in IterateRawGroupValue('ShowOrganizationIndex'):
        if type(CacheValue) != dict:
            continue
        zh = Auxiliary_NormalizeApiTitle(CacheValue.get('title_zh', ''))
        en = Auxiliary_NormalizeDisplayTitle(CacheValue.get('title_en', ''))
        romaji = Auxiliary_NormalizeDisplayTitle(CacheValue.get('title_romaji', ''))
        if zh not in [None, ''] or en not in [None, ''] or romaji not in [None, '']:
            Auxiliary_UpsertCanonicalTitle(zh, en, romaji, 'unknown', [CanonicalKey])
    if ChangedFlag == True:
        PersistentApiCacheDirty = True


def Auxiliary_NormalizeApiTitle(ApiTitle):
    ApiTitle = Auxiliary_NormalizeDisplayTitle(ApiTitle)
    if ApiTitle == '':
        return ''
    ApiTitle = sub(r'第.*?季|Season\s*[0-9]+|S[0-9]{1,2}$','',ApiTitle,flags=I).strip('- []【】 ')
    return ApiTitle


def Auxiliary_GetAbsoluteSourcePath(SourceFilePath):
    SourceFilePath = '' if SourceFilePath in [None, ''] else str(SourceFilePath)
    SourcePathObj = PathlibPath(SourceFilePath)
    if SourcePathObj.is_absolute():
        return SourcePathObj
    BasePath = PathlibPath(Path) if 'Path' in globals() else (
        Runtime.source_path if 'Runtime' in globals() and Runtime else PathlibPath('.')
    )
    return BasePath / SourcePathObj


def Auxiliary_GetSourceFileMTime(SourceFilePath):
    SourcePathObj = Auxiliary_GetAbsoluteSourcePath(SourceFilePath)
    try:
        return float(SourcePathObj.stat().st_mtime)
    except Exception:
        return 0.0


def Auxiliary_BuildEpisodeDecisionKey(CanonicalTitle, SE, EP, FileName):
    CanonicalAliasKey = Auxiliary_NormalizeAliasKey(CanonicalTitle)
    if CanonicalAliasKey == '':
        return None
    SEValue = Auxiliary_FormatSEEPToken(SE)
    EPValue = Auxiliary_FormatSEEPToken(EP)
    FileExt = str(path.splitext(path.basename(str(FileName)))[1]).lower()
    if FileExt in ['.mp4', '.mkv']:
        ExtBucket = 'video'
    elif FileExt in ['.ass', '.srt']:
        ExtBucket = 'subtitle'
    else:
        ExtBucket = FileExt if FileExt not in [None, ''] else 'unknown'
    return f'{CanonicalAliasKey}|{SEValue}|{EPValue}|{ExtBucket}'


def Auxiliary_PreDetectEpisodeHint(FileName):
    QueryName = path.basename(str(FileName))
    NewFile = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(QueryName)))
    if Auxiliary_AnimeFileCheck(NewFile) != True:
        return None
    try:
        RAWEP = Auxiliary_IDEEP(NewFile)
    except Exception:
        return None
    RAWEP, EpisodeSpecialFlag = Auxiliary_NormalizeEpisodeToken(RAWEP, QueryName)
    if RAWEP in [None, '']:
        return None
    BaseTitle = path.splitext(NewFile)[0]
    RAWName = Auxiliary_NormalizeApiTitle(BaseTitle)
    EP = '0' + RAWEP if (len(RAWEP) < 2 or ('.' in RAWEP and RAWEP[0] != '0')) and (SEEPSINGLECHARACTER == False) else RAWEP
    if EpisodeSpecialFlag:
        SE = '00' if SEEPSINGLECHARACTER == False else '0'
        RAWSE = ''
    else:
        SERaw, RSE, _ = Auxiliary_IDE_ParseSeasonTokensFromFile(NewFile)
        SE = '0' + str(SERaw) if len(str(SERaw)) == 1 and SEEPSINGLECHARACTER == False else str(SERaw)
        RAWSE = RSE
    if SEEPSINGLECHARACTER == True:
        SE = SE.lstrip('0')
        EP = EP.lstrip('0')
        SE = SE if SE not in [None, ''] else '0'
        EP = EP if EP not in [None, ''] else '0'
    CanonicalZh, CanonicalID, _ = Auxiliary_ResolveCanonicalTitleByAliases(RAWName)
    CanonicalTitle = CanonicalZh if CanonicalZh not in [None, ''] else RAWName
    EpisodeKey = Auxiliary_BuildEpisodeDecisionKey(CanonicalTitle, SE, EP, QueryName)
    if EpisodeKey in [None, '']:
        return None
    return {
        'EpisodeKey': EpisodeKey,
        'SE': str(SE),
        'EP': str(EP),
        'RAWName': RAWName,
        'ApiName': CanonicalTitle,
        'CanonicalID': CanonicalID if CanonicalID not in [None, ''] else ''
    }


def Auxiliary_GetStandardTitleCacheCandidates(QueryName):
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    if QueryName == '':
        return []

    CandidateList = []

    def AddCandidate(Value):
        Value = Auxiliary_NormalizeDisplayTitle(Value)
        if Value not in [None, ''] and Value not in CandidateList:
            CandidateList.append(Value)

    CompactName = sub(r'\s+',' ',QueryName).strip()
    AddCandidate(QueryName)
    AddCandidate(CompactName)
    AddCandidate(CompactName.replace(' ', '-'))
    AddCandidate(CompactName.replace('-', ' '))
    AddCandidate(CompactName.replace(' ', ''))
    AddCandidate(CompactName.replace('-', ''))
    return CandidateList


def Auxiliary_GetStandardTitleFromCache(QueryName):
    QueryName = Auxiliary_NormalizeDisplayTitle(QueryName)
    if QueryName == '':
        return None
    CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(QueryName)
    if CanonicalZh not in [None, '']:
        return CanonicalZh
    CacheGroupList = [
        ('Bangumi', globals().get('BangumiAPIDataCache', {})),
        ('TMDB', globals().get('TMDBAPIDataCache', {})),
    ]
    for CacheKey in Auxiliary_GetStandardTitleCacheCandidates(QueryName):
        for CacheGroup, InMemoryCache in CacheGroupList:
            if type(InMemoryCache) == dict and CacheKey in InMemoryCache:
                CacheValue = InMemoryCache[CacheKey]
            else:
                CacheValue = Auxiliary_GetPersistentCache(CacheGroup, CacheKey)
                if CacheValue not in [None, ''] and type(InMemoryCache) == dict:
                    InMemoryCache[CacheKey] = CacheValue
            CacheValue = Auxiliary_NormalizeDisplayTitle(CacheValue)
            if CacheValue in [None, '']:
                continue
            CanonicalZh, _, _ = Auxiliary_ResolveCanonicalTitleByAliases(QueryName, CacheValue, CacheKey)
            if CanonicalZh not in [None, '']:
                return CanonicalZh
            CandidateZh = Auxiliary_NormalizeApiTitle(CacheValue)
            CandidateEn = CacheKey if Auxiliary_HasChineseText(CacheKey) == False else ''
            if Auxiliary_HasChineseText(CandidateZh) == False:
                CandidateZh = ''
                if CandidateEn in [None, '']:
                    CandidateEn = CacheValue
            _, CanonicalZh = Auxiliary_UpsertCanonicalTitle(
                CandidateZh,
                CandidateEn,
                '',
                CacheGroup,
                [QueryName, CacheKey, CacheValue]
            )
            if CanonicalZh not in [None, '']:
                return CanonicalZh
            if CandidateZh not in [None, '']:
                return CandidateZh
    return None


def Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(CacheRecord):
    if type(CacheRecord) != dict or all([Key in CacheRecord for Key in ['SE','EP','RAWSE','RAWEP','RAWName']]) != True:
        return CacheRecord, False
    FixedRecord = CacheRecord.copy()
    FixedRecord['RAWEP'] = str(FixedRecord.get('RAWEP', ''))
    FixedRecord['RAWEP'], _ = Auxiliary_NormalizeEpisodeToken(FixedRecord['RAWEP'])
    FixedRecord['RAWName'] = Auxiliary_NormalizeApiTitle(FixedRecord.get('RAWName'))
    FixedRecord['NameEN'] = Auxiliary_NormalizeDisplayTitle(FixedRecord.get('NameEN') or FixedRecord.get('RAWNameEN') or '')
    FixedRecord['NameRomaji'] = Auxiliary_NormalizeDisplayTitle(FixedRecord.get('NameRomaji') or FixedRecord.get('RAWNameRomaji') or '')
    FixedRecord['CanonicalID'] = str(FixedRecord.get('CanonicalID') or '')
    ChangedFlag = False

    CanonicalZh, CanonicalID, _ = Auxiliary_ResolveCanonicalTitleByAliases(
        FixedRecord.get('RAWName'),
        FixedRecord.get('NameEN'),
        FixedRecord.get('NameRomaji')
    )
    if CanonicalZh in [None, '']:
        CachedTitle = Auxiliary_GetStandardTitleFromCache(
            FixedRecord.get('RAWName') or FixedRecord.get('NameEN') or FixedRecord.get('NameRomaji')
        )
        if CachedTitle not in [None, '']:
            CanonicalZh = CachedTitle
    if CanonicalZh not in [None, ''] and CanonicalZh != FixedRecord.get('RAWName'):
        FixedRecord['RAWName'] = CanonicalZh
        ChangedFlag = True
    if CanonicalID not in [None, ''] and CanonicalID != FixedRecord.get('CanonicalID'):
        FixedRecord['CanonicalID'] = CanonicalID
        ChangedFlag = True
    UpsertCanonicalID, UpsertCanonicalZh = Auxiliary_UpsertCanonicalTitle(
        FixedRecord.get('RAWName', ''),
        FixedRecord.get('NameEN', ''),
        FixedRecord.get('NameRomaji', ''),
        'openai_identify',
        [FixedRecord.get('RAWName'), FixedRecord.get('NameEN'), FixedRecord.get('NameRomaji')]
    )
    if UpsertCanonicalID not in [None, ''] and FixedRecord.get('CanonicalID') != UpsertCanonicalID:
        FixedRecord['CanonicalID'] = UpsertCanonicalID
        ChangedFlag = True
    if UpsertCanonicalZh not in [None, ''] and FixedRecord.get('RAWName') != UpsertCanonicalZh:
        FixedRecord['RAWName'] = UpsertCanonicalZh
        ChangedFlag = True
    ContractedZh = Auxiliary_ContractJujutsuKaisenChineseTitle(FixedRecord.get('RAWName', ''))
    if ContractedZh not in [None, ''] and ContractedZh != FixedRecord.get('RAWName'):
        FixedRecord['RAWName'] = ContractedZh
        ChangedFlag = True
        ReUpsertID, ReUpsertZh = Auxiliary_UpsertCanonicalTitle(
            ContractedZh,
            FixedRecord.get('NameEN', ''),
            FixedRecord.get('NameRomaji', ''),
            'openai_identify',
            [ContractedZh, FixedRecord.get('NameEN', ''), FixedRecord.get('NameRomaji', '')]
        )
        if ReUpsertID not in [None, '']:
            FixedRecord['CanonicalID'] = ReUpsertID
            ChangedFlag = True
        if ReUpsertZh not in [None, ''] and ReUpsertZh != FixedRecord.get('RAWName'):
            FixedRecord['RAWName'] = ReUpsertZh
            ChangedFlag = True
    RemapTuple = Auxiliary_RemappedJujutsuKaisenSeasonEpisode(
        FixedRecord.get('RAWSE'),
        FixedRecord.get('RAWEP'),
        FixedRecord.get('SE'),
        FixedRecord.get('EP'),
        FixedRecord.get('NameEN', ''),
        FixedRecord.get('NameRomaji', ''),
        FixedRecord.get('RAWName', '')
    )
    if RemapTuple != None:
        NewRAWSE, NewRAWEP, NewSE, NewEP = RemapTuple
        if (
            NewRAWSE != str(FixedRecord.get('RAWSE', ''))
            or NewRAWEP != str(FixedRecord.get('RAWEP', ''))
            or NewSE != str(FixedRecord.get('SE', ''))
            or NewEP != str(FixedRecord.get('EP', ''))
        ):
            FixedRecord['RAWSE'] = NewRAWSE
            FixedRecord['RAWEP'] = NewRAWEP
            FixedRecord['SE'] = NewSE
            FixedRecord['EP'] = NewEP
            ChangedFlag = True
    return FixedRecord, ChangedFlag


def Auxiliary_ShouldCacheResolvedFileInfo(OperationResult):
    if type(OperationResult) != dict:
        return False
    Status = OperationResult.get('status')
    Message = OperationResult.get('message')
    if Status == 'success':
        return True
    if Status == 'dry-run':
        return True
    if Status == 'skipped' and Message in ['same_file','existing_link_kept','target_exists','newer_duplicate_kept_oldest']:
        return True
    return False


def Auxiliary_OpenAIIdentifyFileInfo(FileName):
    '''通过 OpenAI 一次性识别剧名/剧季/剧集；剧名经 TMDB 中文→Bangumi→TMDB 英文→OpenAI 译中文'''
    global USEOPENAIAPI,OPENAI_IDENTIFY_ALL,OpenAIIdentifyFileMemoryCache,LastOpenAIFileInfoMeta,LastOpenAIIdentifyFailure
    LastOpenAIFileInfoMeta = {}
    LastOpenAIIdentifyFailure = None
    if USEOPENAIAPI != True or OPENAI_IDENTIFY_ALL != True:
        return None
    QueryFileName = path.basename(FileName)
    PromptBaseName = Auxiliary_StripLeadingBracketReleaseTags(QueryFileName)
    InvalidNameSet = {'', 'None', 'none', 'null', '未知', '无法识别', '无法判断', '不确定'}

    def BuildMetaFromRecord(CacheRecord):
        return {
            'NameEN': CacheRecord.get('NameEN', ''),
            'NameRomaji': CacheRecord.get('NameRomaji', ''),
            'CanonicalID': CacheRecord.get('CanonicalID', ''),
            'CanonicalZh': CacheRecord.get('RAWName', '')
        }

    if QueryFileName in OpenAIIdentifyFileMemoryCache:
        CacheRecord = OpenAIIdentifyFileMemoryCache[QueryFileName]
        if type(CacheRecord) == dict and all([Key in CacheRecord for Key in ['SE','EP','RAWSE','RAWEP','RAWName']]):
            FixedRecord, Updated = Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(CacheRecord)
            if Updated == True:
                OpenAIIdentifyFileMemoryCache[QueryFileName] = FixedRecord
                CacheRecord = FixedRecord
            if CacheRecord.get('RAWName') in [None, '']:
                OpenAIIdentifyFileMemoryCache.pop(QueryFileName, None)
                CacheRecord = None
            if CacheRecord != None:
                Auxiliary_Log(f'OpenAI文件识别内存缓存命中 << {CacheRecord}','INFO')
                LastOpenAIFileInfoMeta = BuildMetaFromRecord(CacheRecord)
                return CacheRecord['SE'],CacheRecord['EP'],CacheRecord['RAWSE'],CacheRecord['RAWEP'],CacheRecord['RAWName']

    ApiKey = Auxiliary_GetOpenAIApiKey()
    if ApiKey in [None, '']:
        Auxiliary_Log('OpenAI文件识别需要 OPENAI_API_KEY','WARNING')
        Auxiliary_NoteOpenAIIdentifyFailure('missing_api_key', '未配置 OPENAI_API_KEY', input_basename=QueryFileName)
        return None

    BaseUrl = OPENAI_BASE_URL if OPENAI_BASE_URL not in [None,''] else 'https://api.longcat.chat/openai'
    ModelName = OPENAI_MODEL if OPENAI_MODEL not in [None,''] else 'LongCat-Flash-Chat'
    TimeoutSeconds = Auxiliary_ParseInt(OPENAI_TIMEOUT_SECONDS, 60)
    if TimeoutSeconds <= 0:
        TimeoutSeconds = 60
    RetryTimes = Auxiliary_ParseInt(NETERRRECTRYTIMS, 2)
    if RetryTimes < 0:
        RetryTimes = 0
    HttpData = None
    try:
        for RetryIndex in range(RetryTimes + 1):
            try:
                HttpData = post(
                    f'{BaseUrl.rstrip("/")}/v1/chat/completions',
                    json={
                        'model':ModelName,
                        'temperature':0,
                        'messages':[
                            {
                                'role':'system',
                                'content':(
                                    '你是番剧文件识别助手。请根据用户提供的单个文件名，识别并仅输出 JSON：{"anime_name_zh":"简体中文番剧名","anime_name_en":"英文名或常见英文写法","anime_name_romaji":"罗马音","season":"季数字(未知填1)","episode":"集数字或小数","special":false}。anime_name_zh 必须尽量返回简体中文标准名称；若当前无法确定中文，请保持 anime_name_zh 为空字符串，同时尽可能给出 anime_name_en 或 anime_name_romaji。anime_name_zh、anime_name_en、anime_name_romaji 只允许填写番剧主标题，禁止包含季信息（如 S2、Season 2、2nd Season、第二季等）。不要输出解释文本。'
                                    '文件名最前面的半角方括号 […] 与全角书名号式标签 【…】 中多为字幕组/发行方标记，不是番剧标题；anime_name_zh、anime_name_en、anime_name_romaji 只填作品主标题。'
                                )
                            },
                            {'role':'user','content':PromptBaseName}
                        ]
                    },
                    headers={
                        'Authorization':f'Bearer {ApiKey}',
                        'Content-Type':'application/json',
                        'User-Agent':f'AutoAnimeMv/{Versions}'
                    },
                    timeout=TimeoutSeconds
                )
            except exceptions.RequestException as err:
                if RetryIndex < RetryTimes:
                    Auxiliary_Log(f'OpenAI文件识别请求超时/失败，第{RetryIndex+1}/{RetryTimes+1}次重试: {err}','WARNING')
                    continue
                Auxiliary_Log(f'OpenAI文件识别请求失败: {err}','WARNING')
                Auxiliary_NoteOpenAIIdentifyFailure('http_request_failed', str(err), input_basename=QueryFileName)
                return None
            if HttpData.status_code == 200:
                break
            if RetryIndex < RetryTimes:
                Auxiliary_Log(f'OpenAI文件识别请求失败,状态码 {HttpData.status_code}，第{RetryIndex+1}/{RetryTimes+1}次重试','WARNING')
                continue
            Auxiliary_Log(f'OpenAI文件识别请求失败,状态码 {HttpData.status_code}','WARNING')
            Auxiliary_NoteOpenAIIdentifyFailure('http_status', f'status={HttpData.status_code}', input_basename=QueryFileName)
            return None
        if HttpData in [None, '']:
            Auxiliary_Log('OpenAI文件识别请求失败，未获得有效响应','WARNING')
            Auxiliary_NoteOpenAIIdentifyFailure('no_http_response', '', input_basename=QueryFileName)
            return None
        OpenAIData = HttpData.json()
        if type(OpenAIData) != dict:
            Auxiliary_Log('OpenAI文件识别返回数据结构异常','WARNING')
            Auxiliary_NoteOpenAIIdentifyFailure('response_not_dict', '', input_basename=QueryFileName)
            return None
        Choices = OpenAIData.get('choices', [])
        if type(Choices) != list or Choices == []:
            Auxiliary_Log('OpenAI文件识别返回格式异常: 缺少 choices','WARNING')
            Auxiliary_NoteOpenAIIdentifyFailure('no_choices', '', input_basename=QueryFileName)
            return None
        Message = Choices[0].get('message', {})
        ParsedData = Auxiliary_ParseJsonFromAIContent(Message.get('content', '') if type(Message) == dict else '')
        if type(ParsedData) != dict:
            Auxiliary_Log('OpenAI文件识别返回内容不是有效 JSON','WARNING')
            RawPreview = Message.get('content', '') if type(Message) == dict else ''
            if type(RawPreview) == str and len(RawPreview) > 800:
                RawPreview = RawPreview[:800] + '…'
            Auxiliary_NoteOpenAIIdentifyFailure('content_not_json', 'choices[0].message.content 无法解析为对象', input_basename=QueryFileName, raw_content_preview=RawPreview)
            return None

        NameZH = Auxiliary_NormalizeApiTitle(
            ParsedData.get('anime_name_zh')
            or ParsedData.get('anime_name')
            or ParsedData.get('title')
            or ParsedData.get('name')
            or ''
        )
        NameEN = Auxiliary_NormalizeDisplayTitle(
            ParsedData.get('anime_name_en')
            or ParsedData.get('english_title')
            or ParsedData.get('title_en')
            or ParsedData.get('name_en')
            or ''
        )
        NameRomaji = Auxiliary_NormalizeDisplayTitle(
            ParsedData.get('anime_name_romaji')
            or ParsedData.get('romaji_title')
            or ParsedData.get('title_romaji')
            or ParsedData.get('name_romaji')
            or ''
        )
        if NameZH in InvalidNameSet:
            NameZH = ''
        if NameEN in InvalidNameSet:
            NameEN = ''
        if NameRomaji in InvalidNameSet:
            NameRomaji = ''
        if NameZH not in [None, ''] and Auxiliary_HasChineseText(NameZH) == False:
            NameZH = ''
        AINameZH = NameZH

        RAWEP = Auxiliary_CoalesceEpisodeFromParsed(ParsedData)
        RAWEP, EpisodeSpecialFlag = Auxiliary_NormalizeEpisodeToken(RAWEP, QueryFileName)
        if RAWEP in [None, '']:
            Auxiliary_Log(f'OpenAI文件识别未返回可用剧集: {QueryFileName}','WARNING')
            Snap = {}
            for Key in ('anime_name_zh', 'anime_name_en', 'anime_name_romaji', 'season', 'episode', 'ep', 'se', 'special'):
                if Key in ParsedData:
                    Snap[Key] = ParsedData.get(Key)
            Auxiliary_NoteOpenAIIdentifyFailure(
                'episode_missing',
                'episode/ep 缺失、为空或归一后不可用（注意：整数 0 是合法第 0 集）',
                input_basename=QueryFileName,
                openai_parsed_snapshot=Snap
            )
            return None

        NameZH_out, CanonicalID, NameEN, NameRomaji = Auxiliary_ResolvePlannedTitleChain(AINameZH, NameEN, NameRomaji, FileName)
        RAWName = NameZH_out
        HintInfo = Auxiliary_PreDetectEpisodeHint(QueryFileName)
        if type(HintInfo) == dict:
            HintCanonicalID = str(HintInfo.get('CanonicalID') or '')
            if HintCanonicalID != '':
                HintRecord = Auxiliary_GetCanonicalTitleRecord(HintCanonicalID)
                if type(HintRecord) == dict:
                    HintZh = Auxiliary_NormalizeApiTitle(HintRecord.get('zh', ''))
                    if HintZh not in [None, '']:
                        RAWName = HintZh
                        CanonicalID = HintCanonicalID

        SpecialFlag = Auxiliary_ParseBool(ParsedData.get('special', False))
        if SpecialFlag != True:
            SpecialFlag = EpisodeSpecialFlag
        if SpecialFlag == True:
            SE = '00' if SEEPSINGLECHARACTER == False else '0'
            RAWSE = ''
        else:
            SeasonValue = Auxiliary_CoalesceSeasonFromParsed(ParsedData, '1')
            SeasonValue = sub(r'[^0-9]','',str(SeasonValue).strip()) if SeasonValue not in [None, ''] else '1'
            SeasonValue = '1' if SeasonValue in [None, '', '0'] else SeasonValue
            RAWSE = SeasonValue
            SE = SeasonValue.zfill(2) if SEEPSINGLECHARACTER == False else SeasonValue.lstrip('0')
            if SE in [None, '']:
                SE = '1' if SEEPSINGLECHARACTER == True else '01'

        EP = '0' + RAWEP if (len(RAWEP) < 2 or ('.' in RAWEP and RAWEP[0] != '0')) and (SEEPSINGLECHARACTER == False) else RAWEP
        if SEEPSINGLECHARACTER == True:
            SE = SE.lstrip('0')
            EP = EP.lstrip('0')
            SE = SE if SE not in [None, ''] else '0'
            EP = EP if EP not in [None, ''] else '0'

        CacheRecord = {
            'SE':SE,
            'EP':EP,
            'RAWSE':RAWSE,
            'RAWEP':RAWEP,
            'RAWName':RAWName,
            'NameEN':NameEN,
            'NameRomaji':NameRomaji,
            'CanonicalID':CanonicalID if CanonicalID not in [None, ''] else ''
        }
        CacheRecord, _ = Auxiliary_ApplyStandardTitleCacheToFileInfoRecord(CacheRecord)
        SE = CacheRecord.get('SE', SE)
        EP = CacheRecord.get('EP', EP)
        RAWSE = CacheRecord.get('RAWSE', RAWSE)
        RAWEP = CacheRecord.get('RAWEP', RAWEP)
        RAWName = CacheRecord.get('RAWName', RAWName)
        OpenAIIdentifyFileMemoryCache[QueryFileName] = CacheRecord
        LastOpenAIFileInfoMeta = BuildMetaFromRecord(CacheRecord)
        Auxiliary_Log(f'OpenAI文件识别成功 => 剧名:{RAWName} 季:{SE} 集:{EP}','INFO')
        return SE,EP,RAWSE,RAWEP,RAWName
    except Exception as err:
        Auxiliary_Log(f'OpenAI文件识别处理失败: {err}','WARNING')
        Auxiliary_NoteOpenAIIdentifyFailure('exception', str(err), input_basename=path.basename(FileName))
        return None


def Auxiliary_RecordOperation(Action, SrcPath, DstPath, Status, Message='',BackupPath=''):
    if 'Runtime' not in globals() or Runtime is None:
        return
    Runtime.operation_records.append({
        'timestamp':strftime("%Y-%m-%d %H:%M:%S",localtime(time())),
        'action':Action,
        'src':str(SrcPath),
        'dst':str(DstPath),
        'status':Status,
        'message':Message,
        'backup':str(BackupPath) if BackupPath not in [None, ''] else ''
    })


def Auxiliary_WriteOperationLog():
    if OPERATION_LOG_ENABLE != True or 'Runtime' not in globals() or Runtime is None:
        return
    if RUN_COMMAND == 'rollback':
        return
    if Runtime.operation_log_path in [None, '']:
        return
    try:
        Runtime.operation_log_path.parent.mkdir(parents=True,exist_ok=True)
        Payload = {
            'run_id': CurrentRunID,
            'dry_run': Runtime.config.dry_run,
            'naming_style': Runtime.config.naming_style,
            'records': Runtime.operation_records
        }
        with open(Runtime.operation_log_path,'w',encoding='UTF-8') as LogFile:
            json.dump(Payload,LogFile,ensure_ascii=False,indent=2)
        Auxiliary_Log(f'操作日志已写入 {Runtime.operation_log_path}','INFO')
    except Exception as err:
        Auxiliary_Log(f'操作日志写入失败: {err}','WARNING')


def Auxiliary_RollbackFromLog(LogPath):
    '''根据操作日志回滚文件'''
    RollbackFile = PathlibPath(LogPath)
    if RollbackFile.is_file() == False:
        Auxiliary_Exit(f'回滚日志不存在: {RollbackFile}')
    try:
        with open(RollbackFile,'r',encoding='UTF-8') as ff:
            Data = json.load(ff)
    except json.JSONDecodeError:
        with open(RollbackFile,'r',encoding='UTF-8-sig') as ff:
            Data = json.load(ff)
    Records = Data.get('records', [])
    if type(Records) != list or Records == []:
        Auxiliary_Exit(f'回滚日志内无可用记录: {RollbackFile}')
    for Record in Records[::-1]:
        if type(Record) != dict:
            continue
        if Record.get('status') not in ['success']:
            continue
        Action = Record.get('action')
        SrcPath = PathlibPath(Record.get('src', ''))
        DstPath = PathlibPath(Record.get('dst', ''))
        BackupPath = PathlibPath(Record.get('backup')) if Record.get('backup') not in [None, ''] else None
        try:
            if Action == 'move':
                if DstPath.exists():
                    DstPath.parent.mkdir(parents=True,exist_ok=True)
                    move(str(DstPath), str(SrcPath))
                if BackupPath and BackupPath.exists():
                    move(str(BackupPath), str(DstPath))
            elif Action == 'link':
                if DstPath.exists():
                    remove(str(DstPath))
                if BackupPath and BackupPath.exists():
                    move(str(BackupPath), str(DstPath))
            elif Action == 'remove':
                if BackupPath and BackupPath.exists():
                    move(str(BackupPath), str(DstPath))
            Auxiliary_Log(f'回滚成功: {Action} {DstPath} -> {SrcPath}','INFO')
        except Exception as err:
            Auxiliary_Log(f'回滚失败: {Action} {DstPath} -> {SrcPath}, {err}','WARNING')

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
        '使用系统代理'
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
        'API获取到结果'
    )
    if any(OneMsg.startswith(Prefix) for Prefix in SilentPrefixes):
        return False
    if any(Keyword in OneMsg for Keyword in SilentSubstrings):
        return False
    return True

def Auxiliary_Log(Msg:str,MsgFlag='INFO',flag=None,end='\n'):
    '''日志'''

    global LogData,PRINTLOGFLAG
    Msg = Msg if type(Msg) == tuple else (Msg,)
    for OneMsg in Msg:
        Msg = f'[{strftime("%Y-%m-%d %H:%M:%S",localtime(time()))}] {MsgFlag}: {OneMsg}'
        if (PRINTLOGFLAG == True or flag == 'PRINT') and Auxiliary_ShouldPrintConsoleLog(OneMsg,MsgFlag,flag):
            print(Msg,end=end)         
        LogData = '' + Msg if 'LogData' not in globals() else LogData + '\n' + Msg

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
    if RMLOGSFLAG != False and 'LogsFileList' in globals() and LogsFileList != []:
        ToDay = datetime.strptime(datetime.now().strftime('%Y-%m-%d'),"%Y-%m-%d").date()
        for Logs in LogsFileList:
            LogFileName = path.basename(Logs)
            if match(r'^\d{4}-\d{2}-\d{2}\.log$', LogFileName, flags=I) == None:
                continue
            LogDate = datetime.strptime(LogFileName.replace('.log',''),"%Y-%m-%d").date()
            if (ToDay - LogDate).days >= int(RMLOGSFLAG):
                remove(f'{Path}{Separator}{Logs}')
                RmLogsList.append(Logs)
        if RmLogsList != []:
            Auxiliary_Log(f'清理了保存时间达到和超过{RMLOGSFLAG}天的日志文件 << {RmLogsList}')

def Auxiliary_WriteLog():
    '''写log文件'''

    LogPath = filepath if 'filepath' in globals() and path.exists(filepath) == True else PyPath
    if LogPath in [None, '']:
        LogPath = str(PathlibPath('.').resolve())
    if path.exists(LogPath) == False:
        makedirs(LogPath, exist_ok=True)
    if LogPath == PyPath:
        Auxiliary_Log(f'Log文件保存在工具目录下','WARNING')
    with open(f'{LogPath}{Separator}{strftime("%Y-%m-%d",localtime(time()))}.log','a+',encoding='UTF-8') as LogFile:
        LogFile.write(LogData)

def Auxiliary_UniformOTSTR(File):
    '''统一意外字符'''

    NewFile = convert(File,'zh-hans')# 繁化简
    NewUSTRFile = sub(r',|，| ','-',NewFile,flags=I) 
    # 修复：保留~字符（包括全角和半角），不要替换成=
    NewUSTRFile = sub(r'[^a-z0-9\s&/:：.\-\(\)（）《》\u4e00-\u9fa5\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF°ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ~～]','=',NewUSTRFile,flags=I)
    #异种剧集统一
    OtEpisodesMatchData = [r'第(\d{1,4})集',r'(\d{1,4})集',r'第(\d{1,4})话',r'(\d{1,4})END',r'(\d{1,4}) END',r'(\d{1,4})E']
    for i in OtEpisodesMatchData:
        i = f'[^0-9a-z]{i}[^0-9a-z]'
        if search(i,NewUSTRFile,flags=I) != None:
            a = search(i,NewUSTRFile,flags=I)
            NewUSTRFile = NewUSTRFile.replace(a.group(),'='+a.group(1).strip('\u4e00-\u9fa5')+'=')
    return NewUSTRFile

def Auxiliary_RMOTSTR(File):
    '''剔除意外字符'''

    global FuzzyMatchData
    global PreciseMatchData
    NewPSTRFile = File
    #匹配待去除列表
    FuzzyMatchData = [r'(.*?|=)月新番(.*?|=)',r'\d{4}.\d{2}.\d{2}',r'20\d{2}',r'v[2-9]',r'\d{4}年\d{1,2}月番']
    #精准待去除列表
    PreciseMatchData = [r'仅限港澳台地区',r'年龄限制版',r'国漫',r'x264',r'1080p',r'720p',r'4k',r'（-）']
    for i in PreciseMatchData:
        NewPSTRFile = sub(r'%s'%i,'=',NewPSTRFile,flags=I)
    for i in FuzzyMatchData:
        NewPSTRFile = sub(i,'=',NewPSTRFile,flags=I)
    return NewPSTRFile

def Auxiliary_IDESE(File):
    '''识别剧季并截断Name'''

    SeasonMatchData = r'(季(.*?)第)|(([0-9]{0,1}[0-9]{1})S)|(([0-9]{0,1}[0-9]{1})nosaeS)|(([0-9]{0,1}[0-9]{1}) nosaeS)|(([0-9]{0,1}[0-9]{1})-nosaeS)|(nosaeS-dn([0-9]{1}))|(nosaeS-dr([0-9]{1}))'
    if (X := findall(SeasonMatchData,File[::-1],flags=I)) != []:
        SEData = X
        SENamelist = []
        SEList = []
        for sedata in SEData:
            for se in sedata:# 取值
                if se != '' and se.isnumeric() == False:
                    SENamelist.append(se[::-1])
                #elif len(se) == 1:
                #    SEList.append(se)
                elif se.isnumeric() == True: # 判断数字
                    SEList.append(se)
        for i in SENamelist:# 截断Name
            File = sub(r'%s.*'%i,'',File,flags=I).strip('=') #通过剧季截断文件名
        for i in range(len(SEList)):
            if SEList[i].isdecimal() == True: # 判断纯数字
                SE = SEList[i][::-1]
            elif '\u0e00' <= SEList[i] <= '\u9fa5':# 中文剧季转化
                digit = {'一':'01', '二':'02', '三':'03', '四':'04', '五':'05', '六':'06', '七':'07', '八':'08', '九':'09','壹':'01','贰':'02','叁':'03','肆':'04','伍':'05','陆':'06','柒':'07','捌':'08','玖':'09'}
                SE = digit[SEList[i]]
            if SE is not None:
                return SE,File,SENamelist[0]
    elif (X := findall(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]',File[::-1],flags=I)) != []:
        A = {'Ⅰ':'01','Ⅱ':'02','Ⅲ':'03','Ⅳ':'04','Ⅴ':'05','Ⅵ':'06','Ⅶ':'07','Ⅷ':'08','Ⅸ':'09','Ⅹ':'10','Ⅺ':'11','Ⅻ':'12'}
        return A[X[0]],File,X[0]
    else:
        return '01',File,''

def Auxiliary_IDEEP(File):
    '''识别剧集'''

    try:
        if findall(r'[^0-9.\u4e00-\u9fa5\u0800-\u4e00]([0-9.]{1,4}-[0-9.]{1,4})[^0-9.\u4e00-\u9fa5\u0800-\u4e00]',File[::-1],flags=I) != []:
            Auxiliary_Log('剧集包不予处理','WARNING')
            raise Exception()
        elif (X := findall(r'[^0-9a-z.\u4e00-\u9fa5\u0800-\u4e00]([0-9.]{1,5})[^0-9a-uw-z.\u4e00-\u9fa5\u0800-\u4e00]',File[::-1],flags=I)) != []:
            Episodes = X[0][::-1].strip(" =-_eEv")
        else:
            Episodes = findall(r'[^0-9a-z.\u4e00-\u9fa5\u0800-\u4e00]([0-9]{1,4})[^0-9a-uw-z.\u4e00-\u9fa5\u0800-\u4e00]',File[::-1],flags=I)[0][::-1].strip(" =-_eEv")

    except IndexError:
        Auxiliary_Log('未匹配出剧集,请检查(程序目前不支持电影动漫)','WARNING')
        raise Exception()
    except :
        raise Exception()
    else:
        #Auxiliary_Log(f'匹配出的剧集 ==> {Episodes}','INFO')
        return Episodes

def Auxiliary_RMSubtitlingTeam(File):
    '''剔除字幕组信息'''

    #File = File.strip('=')
    if File[0] == '《':# 判断有无字幕组信息
        File = sub(r'《|》','',File,flags=I) 
    else:
        File = sub(r'^=.*?=','',File,flags=I)
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

def Auxiliary_IDEVDName(File,RAWEP): 
    '''识别剧名'''

    try:
        #VDName = sub(r'.*%s'%RAWEP[::-1],'',File[::-1],count=0,flags=I).strip('=-=-=-')[::-1]
        match_result = search(r'[=|-]%s[=|-](.*)'%RAWEP[::-1],File[::-1],flags=I)
        if match_result:
            VDName = match_result.group(1).strip('=-=-=-')[::-1]
        else:
            # 如果无法通过剧集截断，尝试其他方法
            VDName = sub(r'.*%s'%RAWEP[::-1],'',File[::-1],count=0,flags=I).strip('=-=-=-')[::-1]
            if not VDName or VDName == File:
                # 如果还是无法识别，使用原始文件名（去除扩展名）
                VDName = path.splitext(File)[0]
        Auxiliary_Log(f'通过剧集截断文件名 ==> {VDName}','INFO')
        return VDName
    except Exception as e:
        Auxiliary_Log(f'剧名识别失败，使用原始文件名: {e}','WARNING')
        return path.splitext(File)[0]

def Auxiliary_IDEASS(File,SE,EP,ASSList):
    '''识别当前番剧视频的所属字幕文件'''

    ASSFileList = []
    for ASSFile in ASSList:
        ASSName = Auxiliary_UniformOTSTR(path.basename(ASSFile))
        try:
            ASSEP = Auxiliary_IDEEP(ASSName)
        except Exception:
            Auxiliary_Log(f'字幕文件无法提取剧集，跳过匹配: {ASSFile}','WARNING')
            continue
        if File in ASSName and EP == ASSEP and SE in ASSName:
            ASSFileList.append(ASSFile)
    ASSFileList = None if ASSFileList == [] else ASSFileList
    return ASSFileList

def Auxiliary_FileType(FileName): 
    '''识别文件类型'''

    SuffixList = {'.ass':'ASS','.srt':'ASS','.mp4':'MP4','.mkv':'MP4','.log':'LOG'}
    for FileType in SuffixList:
        if match(FileType[::-1],FileName[::-1],flags=I) != None:
            try :
                return SuffixList[FileType.lower()]
            except :
                Auxiliary_Exit('文件类型不正确')

def Auxiliary_IsIncompleteDownloadFile(FileName) -> bool:
    '''判断是否为未完成下载文件'''

    BaseName = path.basename(str(FileName)).lower()
    IncompleteSuffixes = ('.!qb','.part','.partial','.aria2','.crdownload')
    return BaseName.endswith(IncompleteSuffixes)

def Auxiliary_ScanDIR(Dir,Flag=0) -> list: 
    '''扫描文件目录,返回文件列表'''

    def Scan(RelativeFile):
        FileSuffix = path.splitext(RelativeFile)[1].lower()
        if FileSuffix == '.ass' or FileSuffix == '.srt':
            AssFileList.append(RelativeFile)
        elif FileSuffix == '.log':
            LogsFileList.append(RelativeFile)
        elif FileSuffix == '.mp4' or FileSuffix == '.mkv':
            VDFileList.append(RelativeFile)

    global LogsFileList
    SuffixList = ['.ass','.srt','.mp4','.mkv','.log']
    AssFileList = []
    VDFileList = []
    LogsFileList = []
    RootPath = PathlibPath(Dir)
    OutputRelativePrefix = None
    if 'Runtime' in globals() and Runtime and getattr(Runtime, 'output_path', None):
        try:
            OutputRelativePrefix = str(PathlibPath(Runtime.output_path).resolve().relative_to(RootPath.resolve())).replace('\\','/')
            if OutputRelativePrefix in ['', '.']:
                OutputRelativePrefix = None
        except Exception:
            OutputRelativePrefix = None
    for Entry in RootPath.rglob('*'): # 递归扫描目录，支持子文件夹内文件
        if Entry.is_file() == False:
            continue
        RelativeFile = str(Entry.relative_to(RootPath))
        RelativeFileNormalized = RelativeFile.replace('\\','/')
        if OutputRelativePrefix not in [None, ''] and (
            RelativeFileNormalized == OutputRelativePrefix or RelativeFileNormalized.startswith(f'{OutputRelativePrefix}/')
        ):
            continue
        BaseName = path.basename(RelativeFile)
        if path.splitext(BaseName)[1].lower() not in SuffixList:
            continue
        if Auxiliary_ScanEntryShouldSkip(RelativeFileNormalized, BaseName):
            continue
        if Flag == 0 and search(r'S\d{1,2}E\d{1,4}',BaseName,flags=I) == None:
            Scan(RelativeFile)
        elif Flag == 1 and search(r'S\d{1,2}E\d{1,4}',BaseName,flags=I) != None:
            Scan(RelativeFile)

    if  VDFileList != []:# 判断模式,处理字幕还是视频
        if AssFileList != []:
            Auxiliary_Log(
                (
                    f'发现{len(AssFileList)}个字幕文件 ==> {Auxiliary_FormatListPreview(AssFileList)}',
                    f'发现{len(VDFileList)}个视频文件 ==> {Auxiliary_FormatListPreview(VDFileList)}'
                ),
                'INFO'
            )
            return VDFileList,AssFileList
        else:
            Auxiliary_Log(
                f'发现{len(VDFileList)}个视频文件,没有发现字幕文件 ==> {Auxiliary_FormatListPreview(VDFileList)}',
                'INFO'
            )
            return VDFileList
    elif AssFileList != []:
        Auxiliary_Log(
            (
                f'没有发现任何番剧视频文件,但发现{len(AssFileList)}个字幕文件 ==> {Auxiliary_FormatListPreview(AssFileList)}',
                '只有字幕文件需要处理'
            ),
            'INFO'
        )
        return AssFileList
    else:
        Auxiliary_Exit('没有任何番剧文件')

def Auxiliary_AnimeFileCheck(File):
    '''检查番剧文件'''

    Checklist = ['OP','CM','SP','PV']
    for i in Checklist:
        if search(f'[-=]{i}[-=]',File,flags=I) != None:
            return i
    return True         

def Auxiliary_ASSFileCA(ASSFileName):
    '''字幕文件的语言分类'''

    ASSFileName = path.basename(ASSFileName)
    SubtitleList = [['简','簡','簡體','sc','chs','GB'],['繁','tc','cht','BIG5'],['日','jp']]
    for i in range(len(SubtitleList)):
        for ii in SubtitleList[i]:
            if search(f'[^0-9a-z]{ii[::-1]}[^0-9a-z]',ASSFileName[::-1],flags=I) != None:
                if i == 0:
                    return '.chs' if JELLYFINFORMAT == False else '.简体中文.chi'
                elif i == 1:
                    return '.cht' if JELLYFINFORMAT == False else '.繁体中文.chi'
                elif i == 2:
                    return '.jp'
    return '.other'

def Auxiliary_PROXY(): 
    '''代理'''
    if USEPROXY == True:
        global HTTPPROXY
        global HTTPSPROXY
        global ALLPROXY
        Auxiliary_Log('代理功能开启')
        if USESYSPROXY == True:
            Auxiliary_Log('使用系统代理')
            HTTPPROXY,HTTPSPROXY,a = X if (X:= tuple(getproxies().values())) != () else ('','','')
        environ['http_proxy'] = HTTPPROXY 
        environ['https_proxy'] = HTTPSPROXY 
        environ['all_proxy'] = ALLPROXY 
        
        

def Auxiliary_Http(Url,flag='GET',JsonData=None,ExtraHeaders=None,Timeout=30,ResponseType='text'):
    '''网络请求，支持 JSON 解析与字段校验前置'''

    headers = {'User-Agent':f'AutoAnimeMv/{Versions}'}
    if type(ExtraHeaders) == dict:
        headers.update(ExtraHeaders)
    if 'themoviedb' in Url:
        TMDBToken = Auxiliary_GetTMDBBearerToken()
        if TMDBToken not in [None, '']:
            headers['Authorization'] = f'Bearer {TMDBToken}'
        else:
            Auxiliary_Log('TMDB token 未配置，TMDBApi 将不可用。请设置环境变量 TMDB_BEARER_TOKEN','WARNING')

    RetryTimes = Auxiliary_ParseInt(NETERRRECTRYTIMS, 1)
    if RetryTimes < 0:
        RetryTimes = 0
    for i in range(RetryTimes + 1):
        try:
            if str(flag).upper() != 'GET':
                HttpData = post(Url,json=JsonData,headers=headers,timeout=Timeout)
            else:
                HttpData = get(Url,headers=headers,timeout=Timeout)
            if HttpData.status_code == 200:
                if ResponseType == 'json':
                    try:
                        return HttpData.json()
                    except ValueError:
                        Auxiliary_Log(f'接口返回不是合法 JSON: {Url}','WARNING')
                        return None
                return HttpData.text.replace(r'\/',r'/')
            Auxiliary_Log(f'HttpData Status Code = {HttpData.status_code}','WARNING')
        except exceptions.ConnectionError:
            Auxiliary_Log(f'访问 {Url} 失败,请检查代理与网络连通性','WARNING')
        except exceptions.RequestException as err:
            Auxiliary_Log(f'访问 {Url} 失败: {err}','WARNING')
        except Exception as err:
            Auxiliary_Log(f'访问 {Url} 失败,未能获取到内容: {err}','WARNING')
        Auxiliary_Log(f'第{i+1}/{RetryTimes+1}次尝试失败','WARNING')
    return None

def Auxiliary_Api(Name):
    """按 TMDB 中文→Bangumi→TMDB 英文→OpenAI 译中文 解析剧名（失败则中止整理）"""
    Name = Auxiliary_NormalizeDisplayTitle(Name)
    if Name in [None, '']:
        Auxiliary_Exit('Auxiliary_Api: 空名称')
    if (ManualWhitelistedTitle := Auxiliary_GetManualWhitelistedTitle(Name)) not in [None, '']:
        _, CanonicalManualWhitelisted = Auxiliary_UpsertCanonicalTitle(
            ManualWhitelistedTitle,
            Name if Auxiliary_HasChineseText(Name) == False else '',
            '',
            'manual',
            [Name, ManualWhitelistedTitle]
        )
        return CanonicalManualWhitelisted if CanonicalManualWhitelisted not in [None, ''] else ManualWhitelistedTitle
    if (CanonicalByInput := Auxiliary_GetStandardTitleFromCache(Name)) not in [None, '']:
        return CanonicalByInput
    if 'animename' in globals() and animename not in ['',None]:
        ManualName = Auxiliary_NormalizeApiTitle(animename)
        _, CanonicalManualName = Auxiliary_UpsertCanonicalTitle(ManualName, '', '', 'manual', [Name, animename])
        Auxiliary_Log(f'使用指定的番剧名称 > {CanonicalManualName if CanonicalManualName not in [None, ""] else ManualName}')
        return CanonicalManualName if CanonicalManualName not in [None, ''] else ManualName
    if APIREQUESTSONLYUSECH and (X := search(r'([一-龥]+)',Name.replace('=','').replace('-',''),flags=I)) != None:
        search_name = X.group(1)
    else:
        search_name = Name
    if (CanonicalBySearch := Auxiliary_GetStandardTitleFromCache(search_name)) not in [None, '']:
        return CanonicalBySearch
    AINameZH = search_name if Auxiliary_HasChineseText(search_name) else ''
    NameEN = search_name if Auxiliary_HasChineseText(search_name) == False else ''
    zh, _, _, _ = Auxiliary_ResolvePlannedTitleChain(AINameZH, NameEN, '', search_name)
    return Auxiliary_NormalizeApiTitle(zh)


def Auxiliary_Exit(LogMsg):
    '''因可预见错误离场'''

    Auxiliary_Log(LogMsg,'EXIT',flag='PRINT')
    exit()

if __name__ == '__main__':
    start = time()
    try:
        Start_PATH()
        ArgvData = Start_GetArgv()
        if RUN_COMMAND == 'rollback':
            Auxiliary_RollbackFromLog(ROLLBACK_LOG_PATH)
        else:
            Processing_Main(Processing_Mode(ArgvData))
    except Exception as err:
        Auxiliary_Log(f'没有预料到的错误 > {err}','ERROR',flag='PRINT')
    else:
        end = time()
        Auxiliary_Log(f'一切工作已经完成,用时{end - start}','INFO',flag='PRINT')
    finally:
        Auxiliary_SavePersistentCache()
        Auxiliary_WriteOperationLog()
        if 'HelpMessages' not in globals():
            Auxiliary_WriteLog()
