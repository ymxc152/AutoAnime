"""
autoanime 命令行入口骨架

本模块在当前迁移批次（skeleton/migrate_* 系列 todo）中暂以"引导 + 派发"骨架形式提供：

- 负责：包初始化（state、配置、持久化缓存、zhconv 词典预加载）、
  单文件/目录/qB 回调三态输入归一化、rollback 模式的入口识别。
- 不负责：完整主流水线 / 字幕整理 / 操作日志写盘 / 回滚执行器；
  这些逻辑将随 `migrate_sort` / `migrate_pipeline` / `migrate_cli_full` 等后续 todo 迁移。

函数命名向旧版 `AutoAnimeMv.py` 对齐，使得现有调用方（如未来 qB 回调脚本）
可以直接替换为 `python AutoAnimeMv2.py ...`。
"""

import argparse

from os import path
import sys
from sys import argv
from time import time

from . import state
from .config_loader import (
    Auxiliary_ApplyConfig,
    Auxiliary_InitRuntimeContext,
    Auxiliary_READConfig,
)
from .logging_utils import Auxiliary_Exit, Auxiliary_Log
from .scanning import NormalizeSingleFileInput
from .zhconv_safe import Auxiliary_InitZhconvDictionarySafely


def Start_PATH(**kwargs) -> dict:
    '''新入口的初始化序列。与旧 `Start_PATH` 语义对齐：读取配置、加载缓存、预热 zhconv。'''
    state.init_defaults()
    Auxiliary_InitZhconvDictionarySafely()
    Auxiliary_READConfig()
    Auxiliary_ApplyConfig()
    Auxiliary_InitRuntimeContext()
    from .cache.migrate import Auxiliary_MigrateCacheToV2IfNeeded
    from .cache.manual_whitelist import Auxiliary_LoadManualWhitelist
    from .cache.persistent import Auxiliary_LoadPersistentCache

    # Schema v2：在加载持久化缓存前执行一次性归档/初始化（与 persistent 内二次调用幂等）
    Auxiliary_MigrateCacheToV2IfNeeded()
    Auxiliary_LoadManualWhitelist(force=True)
    Auxiliary_LoadPersistentCache()
    Auxiliary_Log(
        (
            f'当前工具版本为{state.Versions}',
            f'当前操作系统识别码为{__import__("os").name},posix/nt/java对应linux/windows/java虚拟机',
        ),
        'INFO',
    )
    if state.DRY_RUN:
        Auxiliary_Log('当前处于 DRY_RUN 模式，所有操作仅预览不落盘', 'WARNING')
    for k, v in kwargs.items():
        setattr(state, k, v)
    return {'state': state, 'version': state.Versions}


def Start_GetArgv():
    '''命令行参数解析。支持以下形态：

    - `rollback --log <path>`                : 回滚模式
    - `<dir>`                                : 旧目录扫描
    - `<file>`                               : 单文件快捷输入（自动拆 parent + basename）
    - `<dir> <filename> 1`                   : 原 qB 回调保持兼容
    - `<dir> --file <filename>`              : 目录 + 文件名显式
    '''
    if len(argv) == 1:
        _PrintUsageAndExit()

    if argv[1].lower() == 'rollback':
        RollbackParser = argparse.ArgumentParser(prog='AutoAnimeMv2.py rollback', description='根据操作日志执行回滚')
        RollbackParser.add_argument('log_path', nargs='?', help='操作日志路径')
        RollbackParser.add_argument('--log', dest='log_opt', help='操作日志路径')
        Args = RollbackParser.parse_args(argv[2:])
        RollbackPath = Args.log_opt if Args.log_opt else Args.log_path
        if RollbackPath in [None, '']:
            Auxiliary_Exit('rollback 模式需要传入日志路径，例如: python AutoAnimeMv2.py rollback --log operation.json')
        state.RUN_COMMAND = 'rollback'
        state.ROLLBACK_LOG_PATH = RollbackPath
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
    Parser.add_argument('--file', dest='file_opt', help='目录模式下显式指定单个目标文件名')
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
        _PrintUsageAndExit()

    raw_filepath = Args.filepath_opt if Args.filepath_opt else Args.filepath_pos
    # 单文件输入归一化：若位置参数指向文件，拆成目录 + basename
    normalized_dir, auto_names, single_file = NormalizeSingleFileInput(raw_filepath)
    state.filepath = normalized_dir
    state.filename = Args.filename_opt if Args.filename_opt else Args.filename_pos
    state.number = Args.number_opt if Args.number_opt else Args.number_pos
    state.categoryname = Args.categoryname_opt if Args.categoryname_opt else Args.categoryname_pos
    state.animename = Args.animename_opt if Args.animename_opt else None
    state.tag = Args.tag_opt if Args.tag_opt else Args.tag_pos
    state.SingleFileMode = False
    state.SingleFileVideoName = ''
    state.SingleFileSubtitles = []

    if single_file == True and auto_names:
        # 按 plan：单文件模式下 state.filename 指向视频，额外收录同目录字幕
        state.filename = auto_names[0]
        state.SingleFileMode = True
        state.SingleFileVideoName = auto_names[0]
        state.SingleFileSubtitles = list(auto_names[1:]) if len(auto_names) > 1 else []
        if state.number in [None, '']:
            state.number = '1'
    elif Args.file_opt not in [None, '']:
        state.filename = Args.file_opt
        state.SingleFileMode = True
        state.SingleFileVideoName = Args.file_opt
        state.SingleFileSubtitles = []
        if state.number in [None, '']:
            state.number = '1'

    if Args.naming_style_opt not in [None, '']:
        state.NAMING_STYLE = Args.naming_style_opt
    if Args.dry_run_opt:
        state.DRY_RUN = True
    if Args.output_path_opt not in [None, '']:
        state.OUTPUT_PATH = Args.output_path_opt
    if Args.strict_mode_opt not in [None, '']:
        state.STRICT_MODE = True if str(Args.strict_mode_opt).lower() == 'true' else False
    if Args.force_use_link:
        state.USELINK = True
    if Args.force_no_link:
        state.USELINK = False

    for Key in ['filepath', 'filename', 'number', 'categoryname', 'animename', 'tag', 'NAMING_STYLE', 'DRY_RUN', 'OUTPUT_PATH', 'STRICT_MODE', 'USELINK']:
        Auxiliary_Log(f'{Key} < {getattr(state, Key, None)}')

    if state.filepath in [None, ''] or path.exists(state.filepath) == False:
        Auxiliary_Exit('请输入正确的处理目录路径')

    # 重新初始化 runtime，使 filepath/OUTPUT_PATH 等写入生效
    Auxiliary_InitRuntimeContext()

    if state.filename not in [None, ''] and state.number not in [None, '']:
        return state.filepath, state.filename, state.number
    return state.filepath


def _PrintUsageAndExit():
    Auxiliary_Log(
        '用法示例:\n'
        '  python AutoAnimeMv2.py <dir>\n'
        '  python AutoAnimeMv2.py <file>\n'
        '  python AutoAnimeMv2.py <dir> --file <name>\n'
        '  python AutoAnimeMv2.py <dir> <filename> 1\n'
        '  python AutoAnimeMv2.py rollback --log <operation.json>\n',
        'PRINT',
        flag='PRINT',
    )
    Auxiliary_Exit('请查阅以上用法')


def main() -> int:
    '''对外主入口：模块化流水线的完整实现。

    - rollback 命令 -> `autoanime.pipeline.rollback.Auxiliary_RollbackFromLog`
    - 其他命令     -> `autoanime.pipeline.main.Processing_Main(Processing_Mode(ArgvData))`
    '''
    # Windows 默认终端编码为 GBK，提前切换为 UTF-8 避免 UnicodeEncodeError
    if sys.platform == 'win32':
        try:
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8')
            if hasattr(sys.stderr, 'reconfigure'):
                sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass
    start = time()
    try:
        Start_PATH()
        ArgvData = Start_GetArgv()
        _RunPipeline(ArgvData)
    except SystemExit:
        raise
    except Exception as err:
        Auxiliary_Log(f'没有预料到的错误 > {err}', 'ERROR', flag='PRINT')
        return 1
    finally:
        from .cache.persistent import Auxiliary_SavePersistentCache

        # Schema v2 下仅刷新被标记为 dirty 的子文件（organization / titles / api_responses），不全量重写
        Auxiliary_SavePersistentCache(force=False)
    end = time()
    Auxiliary_Log(f'一切工作已经完成,用时{end - start}', 'INFO', flag='PRINT')
    return 0


def _RunPipeline(ArgvData):
    '''运行新的 `autoanime.pipeline.*` 流水线（migrate_sort_pipeline todo）'''
    from .pipeline.main import Processing_Main
    from .pipeline.mode import Processing_Mode
    from .pipeline.operation_log import Auxiliary_WriteOperationLog
    from .pipeline.rollback import Auxiliary_RollbackFromLog

    if state.RUN_COMMAND == 'rollback':
        Auxiliary_RollbackFromLog(state.ROLLBACK_LOG_PATH)
        return
    Processing_Main(Processing_Mode(ArgvData))
    try:
        Auxiliary_WriteOperationLog()
    except Exception:
        pass
