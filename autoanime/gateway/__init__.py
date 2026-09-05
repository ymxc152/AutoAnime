"""下载网关包（E4 M4 闭环）。

模块职责（ARCHITECTURE §0 右列 + Plan §6 第 1/3 项）：

- ``torrents``：bencode 解码 + infohash 纯函数（幂等锚点的确定性底座）；
- ``rss``：RSS 拉取唯一网络出口（httpx async + feedparser to_thread，B6）；
- ``qbittorrent``：下载主网关（同步库 to_thread 包裹；轮询比对 state，A4）；
- ``aria2``：同操作面接口 + fake 测试（拍板 D5，不实测）。

调度器（scheduler 包）经下方的结构化协议 ``DownloadGateway`` 依赖网关，
不 import 具体实现。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autoanime.gateway.aria2 import Aria2Gateway
from autoanime.gateway.qbittorrent import (
    GatewayError,
    QbittorrentGateway,
    is_completed,
    is_failed,
)

__all__ = [
    "Aria2Gateway",
    "DownloadGateway",
    "GatewayError",
    "QbittorrentGateway",
    "is_completed",
    "is_failed",
]


@runtime_checkable
class DownloadGateway(Protocol):
    """调度器视角的下载器操作面（qB / aria2 共同满足的结构化协议）。

    ``add_torrent_bytes`` 收 .torrent 字节、返回 infohash（幂等锚点）；
    ``status`` 返回 qB 风格的规范化状态字典（state/progress/save_path/
    content_path）；``completed_hashes`` 供启动补扫；``files`` 供完成回调
    扫描包内文件（expected 逐文件附载，§6.1）。
    """

    async def add_torrent_bytes(self, data: bytes, *, save_path: str | None = None) -> str: ...

    async def status(self, torrent_hash: str) -> dict[str, object] | None: ...

    async def completed_hashes(self) -> list[str]: ...

    async def files(self, torrent_hash: str) -> list[dict[str, object]]: ...
