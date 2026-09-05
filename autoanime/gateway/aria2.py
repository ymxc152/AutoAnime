"""aria2 下载网关（E4；拍板 D5：只做接口 + 离线测试，不真实实测）。

与 :class:`QbittorrentGateway` 同一操作面（add_torrent_bytes / status /
completed_hashes / files），便于调度器按 ``settings.downloader`` 换绑。
实现走 aria2 JSON-RPC（httpx async）：

- ``aria2.addTorrent``：.torrent base64 提交，返回 GID；infohash 仍本地
  预算（幂等锚点与 qB 一致）；
- ``aria2.tellStatus``：``status``（active/complete/error/removed）+
  ``completedLength/totalLength`` 映射到与 qB 相同的 state/progress 字段；
- ``aria2.tellActive`` + ``secret`` 前缀参数（token 冷却按协议放在首参）。

失败语义同 qB：JSON-RPC error / 连接失败 → ``GatewayError``（文本不含
secret）。完成判定复用 qbittorrent 模块的纯函数（is_completed/is_failed）。
"""

from __future__ import annotations

import base64
import logging

import httpx
from pydantic import SecretStr

from autoanime.gateway import torrents as torrent_files
from autoanime.gateway.qbittorrent import GatewayError, is_completed, is_failed

logger = logging.getLogger(__name__)


class Aria2Gateway:
    """aria2 JSON-RPC 的最小适配（接口契约 + fake 测试；不实测真实端点）。"""

    def __init__(
        self,
        rpc_url: str,
        secret: SecretStr,
        *,
        category: str = "autoanime",
        timeout_s: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._rpc_url = rpc_url
        self._secret = secret
        self._category = category
        self._timeout_s = timeout_s
        # 测试注入口（MockTransport）；生产路径懒创建短生命周期客户端。
        self._client = client

    async def _rpc(self, method: str, params: list[object]) -> object:
        payload = {
            "jsonrpc": "2.0",
            "id": f"autoanime-{method}",
            "method": method,
            "params": [f"token:{self._secret.get_secret_value()}", *params],
        }
        try:
            if self._client is not None:
                response = await self._client.post(self._rpc_url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                    response = await client.post(self._rpc_url, json=payload)
        except httpx.HTTPError as exc:
            raise GatewayError(f"aria2 {method} failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise GatewayError(f"aria2 {method} http {response.status_code}")
        body = response.json()
        if "error" in body:
            raise GatewayError(f"aria2 {method} rpc error")
        return body.get("result")

    async def add_torrent_bytes(self, data: bytes, *, save_path: str | None = None) -> str:
        infohash = torrent_files.torrent_info_hash(data)
        options: dict[str, object] = {}
        if save_path is not None:
            options["dir"] = save_path
        await self._rpc("aria2.addTorrent", [base64.b64encode(data).decode(), [], options])
        return infohash

    async def status(self, torrent_hash: str) -> dict[str, object] | None:
        """按 GID 查询；本网关以 infohash 兼作 GID（提交侧由装配方保证一致）。"""
        try:
            result = await self._rpc(
                "aria2.tellStatus",
                [torrent_hash, ["status", "completedLength", "totalLength", "dir", "files"]],
            )
        except GatewayError:
            return None
        if not isinstance(result, dict):
            return None
        total = float(str(result.get("totalLength") or 0))
        done = float(str(result.get("completedLength") or 0))
        raw_status = str(result.get("status") or "")
        state = {"active": "downloading", "complete": "completed", "error": "error"}.get(
            raw_status, raw_status
        )
        progress = (done / total) if total > 0 else 0.0
        files = result.get("files")
        listing: list[dict[str, object]] = []
        if isinstance(files, list):
            listing = [
                {"name": str(item.get("path", "") if isinstance(item, dict) else ""), "size": 0}
                for item in files
                if isinstance(item, dict)
            ]
        return {
            "hash": torrent_hash,
            "state": state,
            "progress": progress,
            "name": "",
            "save_path": str(result.get("dir") or ""),
            "content_path": str(result.get("dir") or ""),
            "size": int(total),
            "files": listing,
        }

    async def completed_hashes(self) -> list[str]:
        """aria2 无 category：active + stopped 轮询按调用方过滤（v1 返回空实现
        为诚实降级——补扫依赖 qB 的 filter=completed，aria2 侧由 status 逐个
        比对覆盖，本方法仅满足操作面契约）。"""
        return []

    async def files(self, torrent_hash: str) -> list[dict[str, object]]:
        row = await self.status(torrent_hash)
        if row is None:
            return []
        files = row.get("files")
        if isinstance(files, list):
            return [item for item in files if isinstance(item, dict)]
        return []

    def completed(self, state: str | None, progress: float | None) -> bool:
        """暴露与 qB 相同的完成判定（保持操作面行为一致）。"""
        return is_completed(state, progress)

    def failed(self, state: str | None) -> bool:
        return is_failed(state)
