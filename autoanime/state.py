"""
autoanime 全局可变状态容器

- 汇集原 `AutoAnimeMv.py` 中由 `Start_PATH` 初始化的所有 `global` 变量；
- 任一子模块需要读写原 `global XXX` 时，改为 `from . import state` + `state.XXX`；
- 默认值由 `init_defaults()` 还原，`AutoAnimeMv2.py` 启动时调用；
- 若仅在 `tests/` 中按需重置，也可重复调用 `init_defaults()`。
"""

from os import name as os_name
from pathlib import Path as PathlibPath
from time import localtime, strftime, time

from .config_model import Config, RuntimeContext


# =========================================================================
# 版本/进程常量
# =========================================================================
Versions = '3.(4.5).6'
Separator = '\\' if os_name == 'nt' else '/'
PyPath = str(PathlibPath(__file__).resolve().parent.parent)
CurrentRunID = strftime('%Y%m%d_%H%M%S', localtime(time()))


# =========================================================================
# 内存缓存/运行态
# =========================================================================
AimeListCache = None
BgmAPIDataCache: dict = {}
TMDBAPIDataCache: dict = {}
BangumiAPIDataCache: dict = {}
OpenAIAPIDataCache: dict = {}
OpenAIIdentifyFileMemoryCache: dict = {}
ShowOrganizationIndexDataCache: dict = {}
TitleAliasIndexDataCache: dict = {}
CanonicalTitleIndexDataCache: dict = {}
EpisodeDecisionDataCache: dict = {}
LastOpenAIFileInfoMeta: dict = {}
LastIdentificationFromAI: bool = False
LastOpenAIIdentifyFailure = None
LastIdentificationIsMovie: bool = False
PersistentApiCache: dict = {}
PersistentApiCacheDirty: bool = False
CacheSubfileDirty: dict = {
    "organization": False,
    "titles": False,
    "api_responses": False,
}
ManualTitleWhitelistDataCache: dict = {}
ManualTitleWhitelistMTime: float = 0.0
TMDBTvSeasonLayoutMemoryCache: dict = {}
TMDBTvSeriesIdMemoryCache: dict = {}
LastPersistentCacheFlushTime: float = 0.0
LogData: str = ''
TgBotMsgData: str = ''
Runtime: RuntimeContext = RuntimeContext()
ConfigMagdict: dict = {}
HelpMessages: str = ''
LogsFileList: list = []
# AI 失败 -> 回退链路相关（fix_ai_fallback todo）
OpenAIFallbackBreakerStreak: int = 0


# =========================================================================
# 入口参数（原 Start_GetArgv 负责写入）
# =========================================================================
filepath = None
filename = None
number = None
categoryname = None
animename = None
tag = None
# 大写版（原 Processing_Mode 中又写一份大写同名）
Path = None
CategoryName = ''


# =========================================================================
# 配置项（默认值保持与原 AutoAnimeMv.py Start_PATH 一致）
# =========================================================================
USEMODULE = None
USEPROXY = True
USESYSPROXY = True
HTTPPROXY = 'http://127.0.0.1:7890'
HTTPSPROXY = 'http://127.0.0.1:7890'
ALLPROXY = ''
USEBGMAPI = True
USETMDBAPI = True
USEBANGUMIAPI = True
USEOPENAIAPI = True
OPENAI_BASE_URL = 'https://opencode.ai/zen'
OPENAI_BASE_URLS = ''
OPENAI_API_KEY = ''
OPENAI_API_KEYS = ''
OPENAI_API_KEY_ENV = 'OPENAI_API_KEY'
OPENAI_MODEL = 'mimo-v2.5-free'
OPENAI_MODELS = ''
OPENAI_TEMPERATURE = None
OPENAI_TIMEOUT_SECONDS = 60
OPENAI_PRIORITY_FIRST = True
OPENAI_IDENTIFY_ALL = True
OPENAI_KEY_ROTATE_ON_STATUS = '401,429'
OPENAI_KEY_MAX_CONSECUTIVE_FAILURES = 3
TMDB_BEARER_TOKEN = ''
TMDB_BEARER_TOKEN_ENV = 'TMDB_BEARER_TOKEN'
USELINK = True
STRICT_MODE = True
JELLYFINFORMAT = False
USETITLTOEP = True
LINKFAILSUSEMOVEFLAGS = False
PRINTLOGFLAG = True
RMLOGSFLAG = 7
USEBOTFLAG = False
TIMELAPSE = 0
SEEPSINGLECHARACTER = False
NOTLOADEXTLIST: list = []
MANDATORYCOVER = True
NETERRRECTRYTIMS = 2
APIREQUESTSONLYUSECH = False
USEANIMETAG = False
NAMING_STYLE = 'default'
CACHE_DIR = '.cache'
CACHE_TTL_SECONDS = 86400
CACHE_FLUSH_INTERVAL_SECONDS = 60
SCAN_SKIP_PATH_MARKERS: list = []
SCAN_SKIP_NAME_REGEX: list = []
DRY_RUN = False
MAX_FILENAME_LENGTH = 180
OPERATION_LOG_DIR = 'logs'
OPERATION_LOG_ENABLE = True
OUTPUT_PATH = ''
RUN_COMMAND = 'process'
ROLLBACK_LOG_PATH = ''
# AI 失败回退开关（fix_ai_fallback todo）：默认开启，保证 missing_api_key 时不整盘跳过
OPENAI_FALLBACK_ON_FAILURE = True
OPENAI_FALLBACK_BREAKER_THRESHOLD = 5
# 单文件模式相关（cli_single_file todo）
SingleFileMode: bool = False
SingleFileVideoName: str = ''
SingleFileSubtitles: list = []


# =========================================================================
# 旧 AutoAnimeMv 中出现过的额外动态/临时全局
# =========================================================================
FuzzyMatchData: list = []
PreciseMatchData: list = []
Proxy = None


def init_defaults() -> None:
    '''恢复所有内存缓存/运行态到默认值，供新入口启动或单元测试重复调用。'''
    from . import state as self_mod
    self_mod.AimeListCache = None
    self_mod.BgmAPIDataCache = {}
    self_mod.TMDBAPIDataCache = {}
    self_mod.BangumiAPIDataCache = {}
    self_mod.OpenAIAPIDataCache = {}
    self_mod.OpenAIIdentifyFileMemoryCache = {}
    self_mod.ShowOrganizationIndexDataCache = {}
    self_mod.TitleAliasIndexDataCache = {}
    self_mod.CanonicalTitleIndexDataCache = {}
    self_mod.EpisodeDecisionDataCache = {}
    self_mod.LastOpenAIFileInfoMeta = {}
    self_mod.LastIdentificationFromAI = False
    self_mod.LastOpenAIIdentifyFailure = None
    self_mod.LastIdentificationIsMovie = False
    self_mod.PersistentApiCache = {}
    self_mod.PersistentApiCacheDirty = False
    self_mod.CacheSubfileDirty = {
        "organization": False,
        "titles": False,
        "api_responses": False,
    }
    self_mod.ManualTitleWhitelistDataCache = {}
    self_mod.ManualTitleWhitelistMTime = 0.0
    self_mod.TMDBTvSeasonLayoutMemoryCache = {}
    self_mod.TMDBTvSeriesIdMemoryCache = {}
    self_mod.LastPersistentCacheFlushTime = 0.0
    self_mod.LogData = f'\n\n[{strftime("%Y-%m-%d %H:%M:%S", localtime(time()))}] INFO: Running....'
    self_mod.TgBotMsgData = ''
    self_mod.Runtime = RuntimeContext()
    self_mod.ConfigMagdict = {}
    self_mod.HelpMessages = ''
    self_mod.LogsFileList = []
    self_mod.CurrentRunID = strftime('%Y%m%d_%H%M%S', localtime(time()))
    self_mod.OpenAIFallbackBreakerStreak = 0
    self_mod.SingleFileMode = False
    self_mod.SingleFileVideoName = ''
    self_mod.SingleFileSubtitles = []
