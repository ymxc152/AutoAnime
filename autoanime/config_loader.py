"""
autoanime 配置加载与运行时上下文初始化

对应原 `AutoAnimeMv.py` 中：
- `Auxiliary_NormalizeConfigSection`
- `Auxiliary_ParseConfigValue`
- `Auxiliary_MaskConfigValue`
- `Auxiliary_READConfig`
- `Auxiliary_ApplyConfig`
- `Auxiliary_ParseBool` / `Auxiliary_ParseInt`
- `Auxiliary_ParseDelimitedConfigList`
- `Auxiliary_InitRuntimeContext`
- `Auxiliary_GetCacheStorePath`
- `Auxiliary_GetTMDBBearerToken`
- `Auxiliary_GetOpenAIApiKey`
"""

from ast import literal_eval
from os import environ, path
from pathlib import Path as PathlibPath
from re import I, findall, search

from . import state
from .config_model import Config, RuntimeContext
from .logging_utils import Auxiliary_Log


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
        return int(Value)
    except Exception:
        return DefaultValue


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


def Auxiliary_READConfig():
    '''读取外置 config.ini 文件并写入 state.ConfigMagdict'''
    state.ConfigMagdict = {}
    ConfigPath = f'{state.PyPath}{state.Separator}config.ini'
    if path.isfile(ConfigPath) == False:
        return
    with open(ConfigPath, 'r', encoding='UTF-8') as ff:
        Auxiliary_Log('正在读取外置ini文件', 'INFO')
        KeyName = None
        for i in ff.readlines():
            i = i.strip('\n').strip()
            if i == '' or i[0] == ';':
                continue
            if findall(r'\[(.*?)\]', i) != []:
                KeyName = Auxiliary_NormalizeConfigSection(findall(r'\[(.*?)\]', i)[0])
                if KeyName not in state.ConfigMagdict:
                    state.ConfigMagdict[KeyName] = {}
            elif i[0] != '#':
                if KeyName is None:
                    Auxiliary_Log(f'跳过未归属分区的配置行: {i}', 'WARNING')
                    continue
                if '=' not in i:
                    Auxiliary_Log(f'跳过不合法配置行: {i}', 'WARNING')
                    continue
                ConfigItem = i.split("=", 1)
                state.ConfigMagdict[KeyName][ConfigItem[0].strip('- ')] = ConfigItem[1].strip('- ')
    if state.ConfigMagdict != {}:
        ConfigSummary = {sect: list(values.keys()) for sect, values in state.ConfigMagdict.items()}
        Auxiliary_Log(f'读取到配置分区: {ConfigSummary}')
    else:
        Auxiliary_Log('外置ini文件没有配置', 'WARNING')


def Auxiliary_ApplyConfig():
    '''将 ConfigMagdict['#Config'] 下的键值写回 state 模块属性'''
    if '#Config' not in state.ConfigMagdict:
        return
    for ConfigName in state.ConfigMagdict['#Config']:
        ConfigValue = Auxiliary_ParseConfigValue(state.ConfigMagdict['#Config'][ConfigName])
        if hasattr(state, ConfigName):
            setattr(state, ConfigName, ConfigValue)
        else:
            # 保留未列出的配置项，便于兼容扩展模块
            setattr(state, ConfigName, ConfigValue)
        Auxiliary_Log(f'配置 < {ConfigName} = {Auxiliary_MaskConfigValue(ConfigName, ConfigValue)}', 'INFO')
    # 代理初始化依赖配置项，因此放在最后
    from .apis.http import Auxiliary_PROXY
    Auxiliary_PROXY()


def Auxiliary_InitRuntimeContext():
    '''初始化 state.Runtime 运行时上下文'''
    CacheTTL = Auxiliary_ParseInt(state.CACHE_TTL_SECONDS, 86400)
    if CacheTTL < 0:
        CacheTTL = 86400
    NamingStyle = str(state.NAMING_STYLE).strip().lower() if state.NAMING_STYLE not in [None, ''] else 'default'
    if NamingStyle not in ['default', 'emby']:
        NamingStyle = 'default'
    CategoryNameValue = state.categoryname if state.categoryname not in [None, ''] else ''
    SourcePath = state.filepath if state.filepath not in [None, ''] else state.PyPath
    OutputPathValue = state.OUTPUT_PATH if state.OUTPUT_PATH not in [None, ''] else SourcePath
    OutputPathObj = PathlibPath(OutputPathValue)
    if OutputPathObj.is_absolute() == False:
        OutputPathObj = PathlibPath(SourcePath) / OutputPathObj
    state.Runtime = RuntimeContext(
        source_path=PathlibPath(SourcePath),
        output_path=OutputPathObj,
        category_name=CategoryNameValue,
        config=Config(
            naming_style=NamingStyle,
            dry_run=Auxiliary_ParseBool(state.DRY_RUN),
            cache_dir=str(state.CACHE_DIR).strip() if state.CACHE_DIR not in [None, ''] else '.cache',
            cache_ttl_seconds=CacheTTL,
            tmdb_token_env=str(state.TMDB_BEARER_TOKEN_ENV).strip() if state.TMDB_BEARER_TOKEN_ENV not in [None, ''] else 'TMDB_BEARER_TOKEN',
            openai_key_env=str(state.OPENAI_API_KEY_ENV).strip() if state.OPENAI_API_KEY_ENV not in [None, ''] else 'OPENAI_API_KEY',
            openai_identify_all=Auxiliary_ParseBool(state.OPENAI_IDENTIFY_ALL),
            strict_mode=Auxiliary_ParseBool(state.STRICT_MODE),
            output_path=str(OutputPathObj),
        ),
    )
    if state.OPERATION_LOG_ENABLE:
        LogBasePath = state.Runtime.source_path
        if LogBasePath.exists() == False:
            LogBasePath = PathlibPath(state.PyPath)
        OpDirName = str(state.OPERATION_LOG_DIR).strip() if state.OPERATION_LOG_DIR not in [None, ''] else 'logs'
        state.Runtime.operation_log_path = LogBasePath / OpDirName / f'AutoAnime_operations_{state.CurrentRunID}.json'
    if state.RUN_COMMAND == 'rollback' and state.ROLLBACK_LOG_PATH not in [None, '']:
        state.Runtime.rollback_log_path = PathlibPath(state.ROLLBACK_LOG_PATH)


def Auxiliary_GetCacheStorePath() -> PathlibPath:
    '''返回持久化缓存文件路径 .cache/api_cache.json'''
    if state.Runtime and state.Runtime.config and state.Runtime.config.cache_dir not in [None, '']:
        CacheDir = state.Runtime.config.cache_dir
    else:
        CacheDir = '.cache'
    CacheBasePath = PathlibPath(CacheDir)
    if CacheBasePath.is_absolute() == False:
        CacheBasePath = PathlibPath(state.PyPath) / CacheBasePath
    if CacheBasePath.exists() == False:
        CacheBasePath.mkdir(parents=True, exist_ok=True)
    return CacheBasePath / 'api_cache.json'


def Auxiliary_GetTMDBBearerToken():
    TokenValue = state.TMDB_BEARER_TOKEN
    if TokenValue not in [None, '']:
        return str(TokenValue).strip()
    EnvName = state.Runtime.config.tmdb_token_env if state.Runtime and state.Runtime.config else state.TMDB_BEARER_TOKEN_ENV
    EnvName = str(EnvName).strip() if EnvName not in [None, ''] else 'TMDB_BEARER_TOKEN'
    return str(environ.get(EnvName, '')).strip()


def Auxiliary_GetOpenAIApiKey():
    ApiKey = state.OPENAI_API_KEY
    if ApiKey not in [None, '']:
        return str(ApiKey).strip()
    EnvName = state.Runtime.config.openai_key_env if state.Runtime and state.Runtime.config else state.OPENAI_API_KEY_ENV
    EnvName = str(EnvName).strip() if EnvName not in [None, ''] else 'OPENAI_API_KEY'
    return str(environ.get(EnvName, '')).strip()
