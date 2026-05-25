"""
autoanime 配置模型与哨兵常量

提供以下类型：
- `Config`      : 运行期配置快照
- `RuntimeContext` : 运行期上下文（源路径/输出路径/分类名/操作日志等）
- `_OpenAISkipSpIdentification` + `OPENAI_SKIP_SP_IDENTIFICATION` : 哨兵
- `WINDOWS_RESERVED_NAMES` : Windows 保留文件名集合
"""

from dataclasses import dataclass, field
from pathlib import Path as PathlibPath
from typing import Optional


WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
}


class _OpenAISkipSpIdentification:
    '''OpenAI 将文件识别为 SP 特典时的哨兵，主流程跳过整理'''
    pass


OPENAI_SKIP_SP_IDENTIFICATION = _OpenAISkipSpIdentification()


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
