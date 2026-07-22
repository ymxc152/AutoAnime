#!/usr/bin/env python3
# coding: utf-8
"""AutoAnime v3 独立入口。

旧版 ``AutoAnimeMv.py``、``AutoAnimeMv2.py`` 与 ``autoanime/`` 包均不参与运行。
"""

from autoanime_v3.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
