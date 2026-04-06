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
from re import findall,match,search,sub,I # 匹配相关
from shutil import move # 移动File
from ast import literal_eval # srt转化
from typing import Optional
from zhconv import convert # 繁化简
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

def Start_PATH(**kwargs) -> dict:
    '''初始化'''
    # 版本 数据库缓存 Api数据缓存 Log数据集 分隔符
    global Versions,AimeListCache,BgmAPIDataCache,TMDBAPIDataCache,BangumiAPIDataCache,OpenAIAPIDataCache,OpenAIFileInfoDataCache,LogData,Separator,Proxy,TgBotMsgData,PyPath,Runtime,PersistentApiCache,PersistentApiCacheDirty,CurrentRunID,LastIdentificationFromAI
    Versions = '3.(4.5).6'
    AimeListCache = None
    BgmAPIDataCache = {}
    TMDBAPIDataCache = {}
    BangumiAPIDataCache = {}
    OpenAIAPIDataCache = {}
    OpenAIFileInfoDataCache = {}
    LastIdentificationFromAI = False
    PersistentApiCache = {}
    PersistentApiCacheDirty = False
    LogData = f'\n\n[{strftime("%Y-%m-%d %H:%M:%S",localtime(time()))}] INFO: Running....'
    Separator = '\\' if name == 'nt' else '/'
    TgBotMsgData = ''
    PyPath = str(PathlibPath(__file__).resolve().parent)
    CurrentRunID = strftime('%Y%m%d_%H%M%S',localtime(time()))
    Runtime = RuntimeContext()

    global USEMODULE,USEPROXY,USESYSPROXY,HTTPPROXY,HTTPSPROXY,ALLPROXY,USEBGMAPI,USETMDBAPI,USEBANGUMIAPI,USEOPENAIAPI,OPENAI_BASE_URL,OPENAI_API_KEY,OPENAI_API_KEY_ENV,OPENAI_MODEL,OPENAI_TIMEOUT_SECONDS,OPENAI_PRIORITY_FIRST,OPENAI_IDENTIFY_ALL,TMDB_BEARER_TOKEN,TMDB_BEARER_TOKEN_ENV,USELINK,STRICT_MODE,LINKFAILSUSEMOVEFLAGS,USETITLTOEP,PRINTLOGFLAG,RMLOGSFLAG,USEBOTFLAG,TIMELAPSE,SEEPSINGLECHARACTER,JELLYFINFORMAT,NOTLOADEXTLIST,MANDATORYCOVER,NETERRRECTRYTIMS,APIREQUESTSONLYUSECH,USEANIMETAG,NAMING_STYLE,CACHE_DIR,CACHE_TTL_SECONDS,DRY_RUN,MAX_FILENAME_LENGTH,OPERATION_LOG_DIR,OPERATION_LOG_ENABLE,OUTPUT_PATH,RUN_COMMAND,ROLLBACK_LOG_PATH
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
    OPENAI_API_KEY = '' # 不建议写入仓库，请改用环境变量
    OPENAI_API_KEY_ENV = 'OPENAI_API_KEY' # 默认读取该环境变量
    OPENAI_MODEL = 'LongCat-Flash-Chat' # 模型名称
    OPENAI_TIMEOUT_SECONDS = 60 # OpenAI接口超时时间
    OPENAI_PRIORITY_FIRST = True # True时优先使用AI识别
    OPENAI_IDENTIFY_ALL = True # True时由AI直接识别剧名/季/集
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
            for i in FileListTuporList:
                if path.isfile(f'{Path}{Separator}{i}') == True:
                    valid_files.append(i)
                else:
                    Auxiliary_Log(f'{Path}{Separator}{i} 不存在的文件','WARNING')
            if valid_files != []:
                return valid_files  # 元组中唯一有效的文件列表
            Auxiliary_Exit('没有有效的番剧文件')
    else:
        Auxiliary_Exit(f'不存在 {Path} 目录')
   
def Processing_Main(LorT):
    '''核心处理'''
    global LastIdentificationFromAI

    SubtitleFiles = []
    if type(LorT) == tuple: # (视频文件列表,字幕文件列表)
        VideoFiles = LorT[0]
        SubtitleFiles = LorT[1]
    else: # 唯一有效的文件列表
        VideoFiles = LorT

    for SourceFile in VideoFiles:
        File = path.basename(SourceFile)
        if Auxiliary_FileType(File) == 'ASS':
            Auxiliary_Log(f'跳过仅字幕文件主处理: {SourceFile}','INFO')
            continue
        Auxiliary_Log('-'*80,'INFO')
        flag = Processing_Identification(File)
        if flag == None:
            Auxiliary_Log(f'跳过无法识别的文件: {SourceFile}','WARNING')
            continue
        SE,EP,RAWSE,RAWEP,RAWName = flag
        ASSList = Auxiliary_IDEASS(RAWName,RAWSE,RAWEP,SubtitleFiles) if SubtitleFiles != [] else None
        if LastIdentificationFromAI == True:
            if 'animename' in globals() and animename not in ['',None]:
                ApiName = animename
                Auxiliary_Log('当前文件已由 OpenAI 识别季集，剧名使用手动指定 animename','INFO')
            else:
                ApiName = RAWName
                Auxiliary_Log('当前文件已由 OpenAI 直接识别剧名/季/集，跳过二次 API 标准化','INFO')
        else:
            ApiName = Auxiliary_Api(RAWName)
        Sorting_Mv(File,RAWName,SE,EP,ASSList,ApiName,SourceFilePath=SourceFile)

def Processing_Identification(File:str):
    '''识别'''
    global LastIdentificationFromAI
    LastIdentificationFromAI = False

    NewFile = Auxiliary_RMSubtitlingTeam(Auxiliary_RMOTSTR(Auxiliary_UniformOTSTR(File)))# 字符的统一加剔除
    AnimeFileCheckFlag = Auxiliary_AnimeFileCheck(NewFile)
    if AnimeFileCheckFlag == True:
        Auxiliary_Log('-'*80,'INFO')
        OpenAIIdentifyData = Auxiliary_OpenAIIdentifyFileInfo(File)
        if OpenAIIdentifyData != None:
            LastIdentificationFromAI = True
            return OpenAIIdentifyData
        try:
            RAWEP = Auxiliary_IDEEP(NewFile)
        except:
            Auxiliary_Log(f'{File}文件无法处理故跳过','WARNIG')
            return None
        else:
            Auxiliary_Log(f'匹配出的剧集 ==> {RAWEP}','INFO')
            RAWName = Auxiliary_IDEVDName(NewFile,RAWEP)
            EP = '0' + RAWEP if (len(RAWEP) < 2 or ( '.' in RAWEP and RAWEP[0] != '0')) and (SEEPSINGLECHARACTER == False) else RAWEP# 美化剧集
            if '.' in RAWEP or RAWEP == '0' or RAWEP == '00':
                SE = '00' if SEEPSINGLECHARACTER == False else '0'
                RAWSE = ''
                Auxiliary_Log(f'特殊剧季 ==> {SE}','INFO')
                SeasonMatchData = r'(季(.*?)第)|(([0-9]{0,1}[0-9]{1})S)|(([0-9]{0,1}[0-9]{1})nosaeS)|(([0-9]{0,1}[0-9]{1}) nosaeS)|(([0-9]{0,1}[0-9]{1})-nosaeS)|(nosaeS-dn([0-9]{1}))'
                RAWName = sub(SeasonMatchData,'',RAWName[::-1],flags=I)[::-1].strip('-=')
            else:
                SE,Name,RAWSE = Auxiliary_IDESE(RAWName)
                Auxiliary_Log(f'匹配出的剧季 ==> {RAWSE}','INFO')
                RAWName = RAWName if Name == None else Name
                SE = '0' + SE if len(SE) == 1 and SEEPSINGLECHARACTER == False else SE
            if SEEPSINGLECHARACTER == True:
                SE = SE.lstrip('0')
                EP = EP.lstrip('0')
            return SE,EP,RAWSE,RAWEP,RAWName
    else:
        Auxiliary_Log(f'当前文件属于{AnimeFileCheckFlag},跳过处理','INFO')
        return None

def Auxiliary_SanitizePathComponent(Name, MaxLen=None):
    '''清洗文件名/目录名，避免 Windows 非法字符与保留名'''
    if Name in [None, '']:
        Name = 'Unknown'
    Name = str(Name).replace('\n', ' ').replace('\r', ' ')
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
        return

    if DstPath.exists():
        if MANDATORYCOVER != True:
            Auxiliary_Log(f'{DstPath}已存在,故跳过','WARNING')
            Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'skipped','target_exists')
            return
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
        return

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
                        return
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
    except Exception as err:
        if BackupPath not in ['',None] and PathlibPath(BackupPath).exists() and DstPath.exists() == False:
            try:
                move(str(BackupPath),str(DstPath))
            except Exception:
                pass
        Auxiliary_Log(f'文件操作失败 {SrcPath} -> {DstPath}: {err}','ERROR')
        Auxiliary_RecordOperation(ActionName,SrcPath,DstPath,'failed',str(err),BackupPath)


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

    SafeCategory = Auxiliary_SanitizePathComponent(CategoryName, MAX_FILENAME_LENGTH) if CategoryName != '' else ''
    SafeApiName = Auxiliary_SanitizePathComponent(ApiName, MAX_FILENAME_LENGTH)
    SEPad = Auxiliary_FormatSEEPToken(SE)
    EPPad = Auxiliary_FormatSEEPToken(EP)

    BaseDir = Runtime.output_path if 'Runtime' in globals() and Runtime else PathlibPath(Path)
    if SafeCategory != '':
        BaseDir = BaseDir / SafeCategory

    SeasonDirName = f'Season {SEPad}' if NamingStyle == 'emby' else f'Season{SE}'
    NewDir = BaseDir / SafeApiName / Auxiliary_SanitizePathComponent(SeasonDirName, MAX_FILENAME_LENGTH)
    if DryRunMode != True:
        NewDir.mkdir(parents=True,exist_ok=True)
    elif NewDir.exists():
        Auxiliary_Log(f'{NewDir}已存在','INFO')

    if NamingStyle == 'emby':
        EpisodeBaseName = f'{SafeApiName} - S{SEPad}E{EPPad}'
    else:
        EpisodeBaseName = f'S{SE}E{EP}' if USETITLTOEP != True else f'S{SE}E{EP}.{SafeApiName}'
    EpisodeBaseName = Auxiliary_SanitizePathComponent(EpisodeBaseName, MAX_FILENAME_LENGTH)

    if ASSList != None:
        for ASSFile in ASSList:
            FileType = path.splitext(ASSFile)[1].lower()
            ASSBaseName = path.basename(ASSFile)
            if NamingStyle == 'emby':
                NewASSName = Auxiliary_SanitizePathComponent(f'{SafeApiName} - S{SEPad}E{EPPad}{Auxiliary_SubtitleLanguageSuffixForEmby(ASSBaseName)}', MAX_FILENAME_LENGTH)
            else:
                NewASSName = Auxiliary_SanitizePathComponent(EpisodeBaseName + Auxiliary_ASSFileCA(ASSBaseName), MAX_FILENAME_LENGTH)
            DstPath = NewDir / f'{NewASSName}{FileType}'
            SrcPath = PathlibPath(Path) / ASSFile
            Auxiliary_ExecuteFileOperation(SrcPath,DstPath)

    FileType = path.splitext(FileName)[1].lower()
    if FileType in ['.ass','.srt']:
        if NamingStyle == 'emby':
            NewName = Auxiliary_SanitizePathComponent(f'{SafeApiName} - S{SEPad}E{EPPad}{Auxiliary_SubtitleLanguageSuffixForEmby(FileName)}', MAX_FILENAME_LENGTH)
        else:
            NewName = Auxiliary_SanitizePathComponent(EpisodeBaseName + Auxiliary_ASSFileCA(FileName), MAX_FILENAME_LENGTH)
    else:
        NewName = EpisodeBaseName
    DstPath = NewDir / f'{NewName}{FileType}'
    SrcPath = PathlibPath(Path) / SourceFilePath
    Auxiliary_ExecuteFileOperation(SrcPath,DstPath)

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


def Auxiliary_SavePersistentCache(force=False):
    global PersistentApiCacheDirty
    if force != True and PersistentApiCacheDirty != True:
        return
    CacheFilePath = Auxiliary_GetCacheStorePath()
    try:
        with open(CacheFilePath,'w',encoding='UTF-8') as CacheFile:
            json.dump(PersistentApiCache,CacheFile,ensure_ascii=False,indent=2)
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


def Auxiliary_OpenAIIdentifyFileInfo(FileName):
    '''通过 OpenAI 一次性识别剧名/剧季/剧集'''
    global USEOPENAIAPI,OPENAI_IDENTIFY_ALL,OpenAIFileInfoDataCache
    if USEOPENAIAPI != True or OPENAI_IDENTIFY_ALL != True:
        return None
    QueryFileName = path.basename(FileName)
    if QueryFileName in OpenAIFileInfoDataCache:
        CacheRecord = OpenAIFileInfoDataCache[QueryFileName]
        if type(CacheRecord) == dict and all([Key in CacheRecord for Key in ['SE','EP','RAWSE','RAWEP','RAWName']]):
            Auxiliary_Log(f'OpenAI文件识别缓存命中 << {CacheRecord}','INFO')
            return CacheRecord['SE'],CacheRecord['EP'],CacheRecord['RAWSE'],CacheRecord['RAWEP'],CacheRecord['RAWName']

    PersistentRecord = Auxiliary_GetPersistentCache('OpenAIFileInfo', QueryFileName)
    if type(PersistentRecord) == dict and all([Key in PersistentRecord for Key in ['SE','EP','RAWSE','RAWEP','RAWName']]):
        OpenAIFileInfoDataCache[QueryFileName] = PersistentRecord
        Auxiliary_Log(f'OpenAI文件识别持久化缓存命中 << {PersistentRecord}','INFO')
        return PersistentRecord['SE'],PersistentRecord['EP'],PersistentRecord['RAWSE'],PersistentRecord['RAWEP'],PersistentRecord['RAWName']

    ApiKey = Auxiliary_GetOpenAIApiKey()
    if ApiKey in [None, '']:
        Auxiliary_Log('OpenAI文件识别已启用,但未检测到可用密钥,将回退本地识别','WARNING')
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
                    {'role':'system','content':'你是番剧文件识别助手。请根据用户提供的单个文件名，识别并输出标准 JSON：{"anime_name":"剧名","season":"季(数字，未知填1)","episode":"集(数字或小数)","special":false}。只返回 JSON，不要解释。'},
                    {'role':'user','content':QueryFileName}
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
            Auxiliary_Log(f'OpenAI文件识别请求失败,状态码 {HttpData.status_code}','WARNING')
            return None
        OpenAIData = HttpData.json()
        if type(OpenAIData) != dict:
            Auxiliary_Log('OpenAI文件识别返回数据结构异常','WARNING')
            return None
        Choices = OpenAIData.get('choices', [])
        if type(Choices) != list or Choices == []:
            Auxiliary_Log('OpenAI文件识别返回格式异常: 缺少 choices','WARNING')
            return None
        Message = Choices[0].get('message', {})
        ParsedData = Auxiliary_ParseJsonFromAIContent(Message.get('content', '') if type(Message) == dict else '')
        if type(ParsedData) != dict:
            Auxiliary_Log('OpenAI文件识别返回内容不是有效 JSON','WARNING')
            return None
        RAWName = str(ParsedData.get('anime_name') or ParsedData.get('title') or ParsedData.get('name') or '').strip()
        RAWName = sub(r'第.*?季|Season\s*[0-9]+|S[0-9]{1,2}$','',RAWName,flags=I).strip('- []【】 ')
        if RAWName in [None, '', '未知', '无法识别', '无法判断', '不确定']:
            Auxiliary_Log(f'OpenAI文件识别未返回可用剧名: {QueryFileName}','WARNING')
            return None

        RAWEP = str(ParsedData.get('episode') or ParsedData.get('ep') or '').strip()
        if RAWEP in [None, '']:
            Auxiliary_Log(f'OpenAI文件识别未返回可用剧集: {QueryFileName}','WARNING')
            return None

        SpecialFlag = Auxiliary_ParseBool(ParsedData.get('special', False))
        if SpecialFlag == True or '.' in RAWEP or RAWEP in ['0', '00']:
            SE = '00' if SEEPSINGLECHARACTER == False else '0'
            RAWSE = ''
        else:
            SeasonValue = str(ParsedData.get('season') or ParsedData.get('se') or '1').strip()
            SeasonValue = sub(r'[^0-9]','',SeasonValue) if SeasonValue not in [None, ''] else '1'
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

        CacheRecord = {'SE':SE,'EP':EP,'RAWSE':RAWSE,'RAWEP':RAWEP,'RAWName':RAWName}
        OpenAIFileInfoDataCache[QueryFileName] = CacheRecord
        Auxiliary_SetPersistentCache('OpenAIFileInfo', QueryFileName, CacheRecord)
        Auxiliary_Log(f'OpenAI文件识别成功 => 剧名:{RAWName} 季:{SE} 集:{EP}','INFO')
        return SE,EP,RAWSE,RAWEP,RAWName
    except exceptions.RequestException as err:
        Auxiliary_Log(f'OpenAI文件识别请求失败: {err}','WARNING')
        return None
    except Exception as err:
        Auxiliary_Log(f'OpenAI文件识别处理失败: {err}','WARNING')
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

def Auxiliary_Log(Msg:str,MsgFlag='INFO',flag=None,end='\n'):
    '''日志'''

    global LogData,PRINTLOGFLAG
    Msg = Msg if type(Msg) == tuple else (Msg,)
    for OneMsg in Msg:
        Msg = f'[{strftime("%Y-%m-%d %H:%M:%S",localtime(time()))}] {MsgFlag}: {OneMsg}'
        if PRINTLOGFLAG == True or flag == 'PRINT':
            print(Msg,end=end)         
        LogData = '' + Msg if 'LogData' not in globals() else LogData + '\n' + Msg

def Auxiliary_DeleteLogs():
    '''日志清理'''

    RmLogsList = []
    if RMLOGSFLAG != False and 'LogsFileList' in globals() and LogsFileList != []:
        ToDay = datetime.strptime(datetime.now().strftime('%Y-%m-%d'),"%Y-%m-%d").date()
        for Logs in LogsFileList:
            LogDate =  datetime.strptime(Logs.strip('.log'),"%Y-%m-%d").date()
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
    for Entry in RootPath.rglob('*'): # 递归扫描目录，支持子文件夹内文件
        if Entry.is_file() == False:
            continue
        RelativeFile = str(Entry.relative_to(RootPath))
        BaseName = path.basename(RelativeFile)
        if path.splitext(BaseName)[1].lower() not in SuffixList:
            continue
        if Flag == 0 and search(r'S\d{1,2}E\d{1,4}',BaseName,flags=I) == None:
            Scan(RelativeFile)
        elif Flag == 1 and search(r'S\d{1,2}E\d{1,4}',BaseName,flags=I) != None:
            Scan(RelativeFile)

    if  VDFileList != []:# 判断模式,处理字幕还是视频
        if AssFileList != []:
            Auxiliary_Log((f'发现{len(AssFileList)}个字幕文件 ==> {AssFileList}',f'发现{len(VDFileList)}个视频文件 ==> {VDFileList}'),'INFO')
            return VDFileList,AssFileList
        else:
            Auxiliary_Log(f'发现{len(VDFileList)}个视频文件,没有发现字幕文件 ==> {VDFileList}','INFO')
            return VDFileList
    elif AssFileList != []:
        Auxiliary_Log((f'没有发现任何番剧视频文件,但发现{len(AssFileList)}个字幕文件 ==> {AssFileList}','只有字幕文件需要处理'),'INFO')
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
    def NormalizeApiTitle(ApiTitle):
        ApiTitle = '' if ApiTitle in [None, ''] else str(ApiTitle)
        ApiTitle = ApiTitle.strip().split('\n')[0].strip('`"\' ')
        ApiTitle = sub(r'第.*?季|Season\s*[0-9]+|S[0-9]{1,2}$','',ApiTitle,flags=I).strip('- []【】 ')
        return ApiTitle

    def ReadApiCache(CacheGroup, InMemoryCache, QueryName):
        if QueryName in InMemoryCache:
            Auxiliary_Log(f'{InMemoryCache[QueryName]} << {CacheGroup}内存缓存查询结果')
            return InMemoryCache[QueryName]
        CacheValue = Auxiliary_GetPersistentCache(CacheGroup, QueryName)
        if CacheValue not in [None, '']:
            InMemoryCache[QueryName] = CacheValue
            Auxiliary_Log(f'{CacheValue} << {CacheGroup}持久化缓存查询结果')
            return CacheValue
        return None

    def WriteApiCache(CacheGroup, InMemoryCache, QueryName, ApiTitle):
        InMemoryCache[QueryName] = ApiTitle
        Auxiliary_SetPersistentCache(CacheGroup, QueryName, ApiTitle)

    def OpenAIApi(QueryName):
        '''OpenAI兼容Api相关,返回一个标准的中文名称'''
        global USEOPENAIAPI,OpenAIAPIDataCache
        if USEOPENAIAPI != True:
            Auxiliary_Log('没有使用OpenAIApi进行检索')
            return None

        CachedTitle = ReadApiCache('OpenAI', OpenAIAPIDataCache, QueryName)
        if CachedTitle not in [None, '']:
            return CachedTitle

        ApiKey = Auxiliary_GetOpenAIApiKey()
        if ApiKey in [None,'']:
            Auxiliary_Log('OpenAIApi已启用,但未检测到可用密钥,请配置环境变量 OPENAI_API_KEY','WARNING')
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
                        {'role':'system','content':'你是番剧命名助手。请从输入中提取最可能的标准番剧名称。只返回标题，不要解释，不要季数，不要集数。无法判断时只返回空字符串。'},
                        {'role':'user','content':QueryName}
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
                Auxiliary_Log(f'OpenAIApi请求失败,状态码 {HttpData.status_code}','WARNING')
                return None
            OpenAIData = HttpData.json()
            if type(OpenAIData) != dict:
                Auxiliary_Log('OpenAIApi返回数据结构异常','WARNING')
                return None
            Choices = OpenAIData.get('choices', [])
            if type(Choices) != list or Choices == []:
                Auxiliary_Log('OpenAIApi返回格式异常: 缺少 choices','WARNING')
                return None
            Message = Choices[0].get('message', {})
            ApiTitle = NormalizeApiTitle(Message.get('content', '') if type(Message) == dict else '')
            if ApiTitle in ['', 'None', 'none', 'null', '未知', '无法识别', '无法判断', '不确定']:
                Auxiliary_Log(f'OpenAIApi没有检索到关于 {QueryName} 内容','WARNING')
                return None
            Auxiliary_Log(f'{ApiTitle} << OpenAIApi查询结果')
            WriteApiCache('OpenAI', OpenAIAPIDataCache, QueryName, ApiTitle)
            return ApiTitle
        except exceptions.RequestException as err:
            Auxiliary_Log(f'OpenAIApi请求失败: {err}','WARNING')
            return None
        except Exception as err:
            Auxiliary_Log(f'OpenAIApi处理失败: {err}','WARNING')
            return None

    def BgmApi(QueryName):
        '''BgmApi相关,返回一个标准的中文名称'''
        global USEBGMAPI,BgmAPIDataCache
        if USEBGMAPI != True:
            Auxiliary_Log('没有使用BgmApi进行检索')
            return None
        CachedTitle = ReadApiCache('BGM', BgmAPIDataCache, QueryName)
        if CachedTitle not in [None, '']:
            return CachedTitle

        BgmApiData = Auxiliary_Http(
            f"https://api.bgm.tv/search/subject/{quote(QueryName)}?type=2&responseGroup=small&max_results=1",
            ResponseType='json',
            Timeout=20
        )
        if type(BgmApiData) != dict:
            Auxiliary_Log(f'BgmApi没有检索到关于 {QueryName} 内容','WARNING')
            return None
        ResultList = BgmApiData.get('list', [])
        if type(ResultList) != list or ResultList == [] or type(ResultList[0]) != dict:
            Auxiliary_Log(f'BgmApi返回为空: {QueryName}','WARNING')
            return None
        FirstAnime = ResultList[0]
        ApiTitle = NormalizeApiTitle(FirstAnime.get('name_cn') or FirstAnime.get('name') or '')
        if ApiTitle in [None, '']:
            Auxiliary_Log(f'BgmApi未返回可用标题: {QueryName}','WARNING')
            return None
        Auxiliary_Log(f'{ApiTitle} << BgmApi查询结果')
        WriteApiCache('BGM', BgmAPIDataCache, QueryName, ApiTitle)
        return ApiTitle

    def TMDBApi(QueryName): 
        '''TMDBApi相关,返回一个标准的中文名称'''
        global USETMDBAPI,TMDBAPIDataCache
        if USETMDBAPI != True:
            Auxiliary_Log('没有使用TMDBApi进行检索')
            return None
        if Auxiliary_GetTMDBBearerToken() in [None, '']:
            Auxiliary_Log('TMDBApi 已启用但未配置 token，跳过 TMDB 查询','WARNING')
            return None
        CachedTitle = ReadApiCache('TMDB', TMDBAPIDataCache, QueryName)
        if CachedTitle not in [None, '']:
            return CachedTitle

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
        FirstTV = ResultList[0] if type(ResultList[0]) == dict else {}
        ApiTitle = NormalizeApiTitle(FirstTV.get('name') or FirstTV.get('original_name') or '')
        if ApiTitle in [None, '']:
            Auxiliary_Log(f'TMDBApi未返回可用标题: {QueryName}','WARNING')
            return None
        Auxiliary_Log(f'{ApiTitle} << TMDBApi查询结果')
        WriteApiCache('TMDB', TMDBAPIDataCache, QueryName, ApiTitle)
        return ApiTitle

    def BangumiApi(QueryName):
        '''BangumiApi相关,返回一个标准的中文名称'''
        global USEBANGUMIAPI,BangumiAPIDataCache
        if USEBANGUMIAPI != True:
            Auxiliary_Log('没有使用BangumiApi进行检索')
            return None
        CachedTitle = ReadApiCache('Bangumi', BangumiAPIDataCache, QueryName)
        if CachedTitle not in [None, '']:
            return CachedTitle

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
        ApiTitle = NormalizeApiTitle(AnimeData.get('name_cn') or AnimeData.get('name') or '')
        if ApiTitle in [None, '']:
            Auxiliary_Log(f'BangumiApi未返回可用标题: {QueryName}','WARNING')
            return None
        Auxiliary_Log(f'{ApiTitle} << BangumiApi查询结果')
        WriteApiCache('Bangumi', BangumiAPIDataCache, QueryName, ApiTitle)
        return ApiTitle

    # API轮询机制
    def try_apis(search_name):
        '''尝试所有可用的API，按优先级顺序'''
        apis_to_try = []
        if USEOPENAIAPI and OPENAI_PRIORITY_FIRST:
            apis_to_try.append(('OpenAI', OpenAIApi, search_name))
        
        # 中文优化：优先使用Bangumi API
        if USEBANGUMIAPI:
            apis_to_try.append(('Bangumi', BangumiApi, search_name))
        
        # 主要API
        if USEBGMAPI:
            apis_to_try.append(('BGM', BgmApi, search_name))
        
        # 备用API
        if USETMDBAPI:
            apis_to_try.append(('TMDB', TMDBApi, search_name))
        
        if USEOPENAIAPI and OPENAI_PRIORITY_FIRST == False:
            apis_to_try.append(('OpenAI', OpenAIApi, search_name))
        
        # 尝试每个API
        for api_name, api_func, name in apis_to_try:
            try:
                result = api_func(name)
                if result:
                    Auxiliary_Log(f'成功通过 {api_name} API获取到结果','INFO')
                    return result
            except Exception as e:
                Auxiliary_Log(f'{api_name} API调用失败: {e}','WARNING')
                continue
        
        return None
    
    if 'animename' in globals() and animename not in ['',None]:
        Auxiliary_Log(f'使用指定的番剧名称 > {animename}')
        return animename
    else:
        # 处理搜索名称
        if APIREQUESTSONLYUSECH and (X := search(r'([\u4e00-\u9fa5]+)',Name.replace('=','').replace('-',''),flags=I)) != None:
            search_name = X.group(1)
        else:
            search_name = Name
        
        # 使用API轮询机制
        ApiName = try_apis(search_name)
        
        if ApiName == None:
            Auxiliary_Log(f'所有API识别失败，使用原始名称: {Name}','WARNING')
            return Name
        else:
            return ApiName.replace(' ','') if ApiName != None else ApiName

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
