"""调度时钟（E4）：可注入的时间源。

调度服务（轮询/降频/缺集判定）一律经 ``Clock`` 取当前时间，单测注入
``FrozenClock`` 验证降频与缺集 diff（验收线：注入 clock 的单测）。
系统路径用 ``SystemClock``（本地时间，与库内其余 datetime.now 语义一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """生产时钟。"""

    def now(self) -> datetime:
        return datetime.now()


class FrozenClock:
    """测试时钟：显式推进，绝不自己走。"""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta
