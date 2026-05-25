#!/usr/bin/python3
# coding:utf-8
"""
AutoAnimeMv2 - 模块化重构后的新入口

与旧入口 `AutoAnimeMv.py` 并存，复用 `autoanime/` 包提供的模块化实现：

- `python AutoAnimeMv2.py <dir>`                原目录扫描
- `python AutoAnimeMv2.py <file>`               单文件整理（自动拆 parent + basename，收同目录同集字幕）
- `python AutoAnimeMv2.py <dir> --file <name>`  目录模式 + 明确指定文件
- `python AutoAnimeMv2.py <dir> <filename> 1`   原 qB 回调兼容
- `python AutoAnimeMv2.py rollback --log ...`   回滚模式

本文件本身只做入口薄壳，具体逻辑都在 `autoanime.cli.main`。
"""

from autoanime.cli import main


if __name__ == '__main__':
    raise SystemExit(main())
