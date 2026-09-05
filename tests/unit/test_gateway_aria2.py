"""gateway.aria2 单测（E4a；拍板 D5：接口 + 离线 fake，不真实实测）。

JSON-RPC 交互走 ``httpx.MockTransport`` 回放响应；断言与 qB 网关同一
规范化字段面（state/progress）。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from autoanime.gateway.aria2 import Aria2Gateway
from autoanime.gateway.qbittorrent import GatewayError
from autoanime.gateway.torrents import bencode


def _make_torrent() -> bytes:
    return bencode({"info": {"name": "Show - 02", "length": 5}})


def _rpc_result(result: object) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "result": result})


async def test_add_torrent_posts_base64_and_returns_local_infohash() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["method"] = body["method"]
        seen["params"] = body["params"]
        return _rpc_result("gid-1")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gateway = Aria2Gateway(
            "http://aria2/jsonrpc", SecretStr("s3cret"), client=client
        )
        data = _make_torrent()
        infohash = await gateway.add_torrent_bytes(data, save_path="/downloads")
    assert seen["method"] == "aria2.addTorrent"
    params = seen["params"]
    assert isinstance(params, list)
    assert params[0] == "token:s3cret"  # secret 走协议首参
    assert base64.b64decode(str(params[1])) == data
    assert params[2] == []  # uris 占位
    assert params[3] == {"dir": "/downloads"}
    assert len(infohash) == 40


async def test_status_maps_active_and_complete() -> None:
    responses = iter(
        [
            _rpc_result(
                {
                    "status": "active",
                    "completedLength": "50",
                    "totalLength": "100",
                    "dir": "/downloads",
                }
            ),
            _rpc_result(
                {
                    "status": "complete",
                    "completedLength": "100",
                    "totalLength": "100",
                    "dir": "/downloads",
                }
            ),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gateway = Aria2Gateway("http://aria2/jsonrpc", SecretStr("s"), client=client)
        row = await gateway.status("gid-1")
        assert row is not None
        assert row["state"] == "downloading"
        assert row["progress"] == pytest.approx(0.5)
        assert gateway.completed("downloading", 0.5) is False
        row2 = await gateway.status("gid-1")
        assert row2 is not None
        assert row2["state"] == "completed"
        assert gateway.completed("completed", 1.0) is True


async def test_rpc_error_wraps_gateway_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": 1}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gateway = Aria2Gateway("http://aria2/jsonrpc", SecretStr("s"), client=client)
        with pytest.raises(GatewayError):
            await gateway.add_torrent_bytes(_make_torrent())


async def test_completed_hashes_is_honest_empty() -> None:
    gateway = Aria2Gateway("http://aria2/jsonrpc", SecretStr("s"))
    assert await gateway.completed_hashes() == []
