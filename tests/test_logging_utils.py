# -*- coding: utf-8 -*-
"""日志工具单元测试：覆盖 Windows 控制台编码崩溃场景。"""

import io
import sys

import pytest

from autoanime import state
from autoanime.logging_utils import Auxiliary_Log


def test_auxiliary_log_does_not_raise_on_windows_gbk_console(monkeypatch):
    """模拟 Windows 默认 GBK 终端，输出含 \uff65 中点时不应抛异常。"""

    # 强制进入控制台打印分支
    monkeypatch.setattr(state, 'PRINTLOGFLAG', True)

    # 用 GBK 编码的 stdout 模拟 Windows 默认终端
    fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding='gbk', errors='strict')
    monkeypatch.setattr(sys, 'stdout', fake_stdout)

    # 如果未做防御，这会抛出 UnicodeEncodeError
    Auxiliary_Log('中点测试 \uff65', flag='PRINT')

    # 同时验证日志数据仍保留原始 Unicode
    assert '\uff65' in state.LogData


def test_auxiliary_log_keeps_unicode_in_log_data(monkeypatch):
    """即使控制台无法编码，state.LogData 也应保留完整 Unicode。"""

    monkeypatch.setattr(state, 'PRINTLOGFLAG', True)
    fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding='cp936', errors='strict')
    monkeypatch.setattr(sys, 'stdout', fake_stdout)

    Auxiliary_Log('中点测试 \uff65')
    assert '中点测试 \uff65' in state.LogData
