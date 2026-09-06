"""qBittorrent 下载网关（E4 M4 闭环）：添加 / 进度 / 完成 / 文件清单。

边界（铁律 3 + 审核 B6/A4）：
- qbittorrent-api 是同步库，全部调用 ``asyncio.to_thread`` 包裹（否则
  AsyncIOScheduler/事件循环被阻塞，SSE 心跳停摆）；
- qBittorrent 无 webhook：完成事件由轮询比对 ``state``/``progress`` 得出
  （A4），``is_completed``/``is_failed`` 是纯函数，离线单测钉死；
- 幂等锚点是 infohash：添加一律「先取 .torrent 字节 → 本地算 infohash
  （gateway.torrents）→ 按文件字节提交」，qB 按 URL 添加不回传 hash，
  按字节添加才可让 ``release_record.torrent_hash`` 唯一约束兜底；
- ``category`` 标记隔离本项目的任务：轮询/补扫只看本 category，不碰
  用户其他种子（D21 旧种不动的读侧边界）。

失败语义：任何客户端错误（连接/登录/HTTP）→ ``GatewayError``，文本只含
异常类型与操作名，不含密码。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from autoanime.gateway import torrents as torrent_files

if TYPE_CHECKING:
    import qbittorrentapi

logger = logging.getLogger(__name__)

#: qBittorrent 状态中代表「任务失败」的集合（4.x/5.x 命名差异均已覆盖）。
QB_FAILED_STATES = frozenset({"error", "missingFiles"})
#: qBittorrent 状态中代表「数据已完整」的集合（下载/做种两侧命名都算）。
QB_COMPLETED_STATES = frozenset(
    {
        "uploading",
        "pausedUP",
        "stoppedUP",
        "stalledUP",
        "queuedUP",
        "forcedUP",
        "checkingUP",
    }
)


def is_completed(state: str | None, progress: float | None) -> bool:
    """轮询比对（A4）：progress 满格或状态进入完成集合即算完成。"""
    if progress is not None and progress >= 1.0:
        return True
    return state is not None and state in QB_COMPLETED_STATES


def is_failed(state: str | None) -> bool:
    return state is not None and state in QB_FAILED_STATES


class GatewayError(Exception):
    """下载器调用失败；文本不含密钥。"""


class QbittorrentGateway:
    """qbittorrent-api 的异步薄包装：会话与线程边界都在本类内部。"""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: SecretStr,
        *,
        category: str = "autoanime",
        timeout_s: float = 15.0,
        client: Any | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._category = category
        self._timeout_s = timeout_s
        # 注入口对测试开放（FakeQbClient）；运行时惰性创建真客户端。
        self._client: Any = client

    # --- client lifecycle ---------------------------------------------------

    def _get_client(self) -> qbittorrentapi.Client:
        if self._client is None:
            import qbittorrentapi

            self._client = qbittorrentapi.Client(
                host=self._host,
                port=self._port,
                username=self._username,
                password=self._password.get_secret_value(),
                REQUESTS_ARGS={"timeout": (5, self._timeout_s)},
            )
        return self._client

    async def _call(self, operation: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:
            raise GatewayError(f"qbittorrent {operation} failed: {type(exc).__name__}") from None

    async def ping(self) -> bool:
        """登录探测（启动补扫前判断网关是否可用；失败不 crash）。"""
        try:
            client: Any = self._get_client()
        except Exception as exc:
            raise GatewayError(f"qbittorrent client init failed: {type(exc).__name__}") from None
        # qbittorrent-api 无完整类型标注：局部 Any 化后正常属性访问。
        # 登录方法名是 auth_log_in（auth_logon 在 qbittorrent-api 2026.x
        # 不存在；旧名会让 ping() 在参数求值时抛 AttributeError 而非
        # GatewayError——R1 验收实测修复）。
        logon = getattr(client, "auth_log_in", None) or getattr(
            client, "auth_logon", None
        )
        if logon is None:
            raise GatewayError("qbittorrent auth_log_in failed: AttributeError")
        await self._call("auth_log_in", logon)
        return True

    # --- submission ---------------------------------------------------------

    async def add_torrent_bytes(self, data: bytes, *, save_path: str | None = None) -> str:
        """按 .torrent 字节提交；返回本地预算的 infohash（幂等锚点）。"""
        infohash = torrent_files.torrent_info_hash(data)
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "torrent_files": data,
            "category": self._category,
            "skip_checking": False,
        }
        if save_path is not None:
            kwargs["save_path"] = save_path
        result = await self._call("torrents_add", client.torrents_add, **kwargs)
        if isinstance(result, str) and "fail" in result.lower():
            raise GatewayError(f"qbittorrent torrents_add rejected: {result.strip()}")
        return infohash

    # --- polling ------------------------------------------------------------

    async def status(self, torrent_hash: str) -> dict[str, object] | None:
        """单任务状态（轮询比对）；任务不存在返回 None（按失败处理）。"""
        client = self._get_client()
        rows = await self._call("torrents_info", client.torrents_info, torrent_hashes=torrent_hash)
        for row in rows or []:
            if str(getattr(row, "hash", "")) == torrent_hash:
                return {
                    "hash": torrent_hash,
                    "state": str(getattr(row, "state", "") or ""),
                    "progress": float(getattr(row, "progress", 0.0) or 0.0),
                    "name": str(getattr(row, "name", "") or ""),
                    "save_path": str(getattr(row, "save_path", "") or ""),
                    "content_path": str(getattr(row, "content_path", "") or ""),
                    "size": int(getattr(row, "size", 0) or 0),
                }
        return None

    async def completed_hashes(self) -> list[str]:
        """本 category 下已完成的任务（启动补扫「完成未归档」悬挂任务用）。"""
        client = self._get_client()
        rows = await self._call(
            "torrents_info", client.torrents_info, category=self._category, filter="completed"
        )
        return [str(getattr(row, "hash", "")) for row in rows or [] if getattr(row, "hash", None)]

    async def files(self, torrent_hash: str) -> list[dict[str, object]]:
        """任务内文件清单（完成回调扫描包内视频/字幕用）。"""
        client = self._get_client()
        rows = await self._call("torrents_files", client.torrents_files, torrent_hash=torrent_hash)
        return [
            {
                "name": str(getattr(row, "name", "") or ""),
                "size": int(getattr(row, "size", 0) or 0),
            }
            for row in rows or []
        ]
