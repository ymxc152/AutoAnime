"""
AutoAnimeMv 模块化包骨架

- 为新入口 `AutoAnimeMv2.py` 提供模块化实现；
- 原入口 `AutoAnimeMv.py` 保持不变，与本包并存；
- 公共可变状态统一放在 `autoanime.state`，避免各模块散布 `global` 声明。
"""

__version__ = '3.(4.5).6'

VERSION = __version__

PACKAGE_NAME = 'autoanime'
