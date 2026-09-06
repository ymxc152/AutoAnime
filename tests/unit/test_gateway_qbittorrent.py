"""gateway.qbittorrent 单测（E4a）：纯判定函数 + fake 客户端离线行为。

真连 qBittorrent 属 L2 外发（拍板 D5），本文件全部离线：qBittorrent-api
客户端以 fake 替换（覆盖 _get_client 的注入位），语义对齐 qbittorrent-api
4.x/5.x 的字段形状。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from autoanime.gateway.qbittorrent import (
    GatewayError,
    QbittorrentGateway,
    is_completed,
    is_failed,
)


class FakeQbClient:
    """qbittorrent-api.Client 的最小 fake；按哈希记录种子行。"""

    def __init__(self) -> None:
        self.torrents: dict[str, SimpleNamespace] = {}
        self.files_by_hash: dict[str, list[SimpleNamespace]] = {}
        self.reject_next_add = False
        self.fail_next_call = False
        self.auth_logon_calls = 0

    # 真实 qbittorrent-api 的登录方法名是 auth_log_in（R1 验收修复对齐）。
    def auth_log_in(self) -> None:
        self.auth_logon_calls += 1
        if self.fail_next_call:
            raise RuntimeError("login down")

    def torrents_add(self, **kwargs: object) -> str:
        if self.reject_next_add:
            return "Fails."
        data = kwargs.get("torrent_files")
        assert isinstance(data, bytes)
        from autoanime.gateway import torrents as torrent_files

        infohash = torrent_files.torrent_info_hash(data)
        self.torrents[infohash] = SimpleNamespace(
            hash=infohash,
            state="downloading",
            progress=0.0,
            name="Test",
            save_path="/downloads",
            content_path="/downloads/Test",
            size=123,
        )
        return "Ok."

    def torrents_info(self, **kwargs: object) -> list[SimpleNamespace]:
        if self.fail_next_call:
            raise RuntimeError("api down")
        torrent_hash = kwargs.get("torrent_hashes")
        rows = list(self.torrents.values())
        if torrent_hash:
            rows = [row for row in rows if row.hash == torrent_hash]
        if kwargs.get("filter") == "completed":
            rows = [
                row for row in rows if is_completed(row.state, row.progress)
            ]
        return rows

    def torrents_files(self, *, torrent_hash: str) -> list[SimpleNamespace]:
        return self.files_by_hash.get(torrent_hash, [])


@pytest.mark.parametrize(
    ("state", "progress", "expected"),
    [
        ("downloading", 0.5, False),
        ("downloading", 1.0, True),
        ("stalledUP", None, True),
        ("pausedUP", 0.9, True),
        ("stoppedUP", None, True),
        ("uploading", 0.0, True),
        ("metaDL", 0.1, False),
        (None, None, False),
    ],
)
def test_is_completed(state: str | None, progress: float | None, expected: bool) -> None:
    assert is_completed(state, progress) is expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [("error", True), ("missingFiles", True), ("downloading", False), (None, False)],
)
def test_is_failed(state: str | None, expected: bool) -> None:
    assert is_failed(state) is expected


def _make_torrent(name: str = "Test") -> bytes:
    from autoanime.gateway.torrents import bencode

    return bencode({"info": {"name": name, "length": 5}})


async def test_add_torrent_bytes_computes_infohash_and_registers() -> None:
    fake = FakeQbClient()
    gateway = QbittorrentGateway("h", 1, "u", SecretStr("p"), client=fake)
    data = _make_torrent("Show - 01")
    infohash = await gateway.add_torrent_bytes(data, save_path="/downloads")
    assert infohash in fake.torrents
    assert fake.torrents[infohash].save_path == "/downloads"


async def test_add_rejected_raises_gateway_error() -> None:
    fake = FakeQbClient()
    gateway = QbittorrentGateway("h", 1, "u", SecretStr("p"), client=fake)
    fake.reject_next_add = True
    with pytest.raises(GatewayError):
        await gateway.add_torrent_bytes(_make_torrent())


async def test_status_maps_fields_and_missing_returns_none() -> None:
    fake = FakeQbClient()
    gateway = QbittorrentGateway("h", 1, "u", SecretStr("p"), client=fake)
    infohash = await gateway.add_torrent_bytes(_make_torrent())
    row = await gateway.status(infohash)
    assert row is not None
    assert row["hash"] == infohash
    assert row["state"] == "downloading"
    assert row["progress"] == 0.0
    assert await gateway.status("deadbeef") is None


async def test_completed_hashes_filters_by_category_and_state() -> None:
    fake = FakeQbClient()
    gateway = QbittorrentGateway("h", 1, "u", SecretStr("p"), client=fake)
    infohash = await gateway.add_torrent_bytes(_make_torrent())
    assert await gateway.completed_hashes() == []
    fake.torrents[infohash].progress = 1.0
    assert await gateway.completed_hashes() == [infohash]


async def test_files_listing() -> None:
    fake = FakeQbClient()
    gateway = QbittorrentGateway("h", 1, "u", SecretStr("p"), client=fake)
    infohash = await gateway.add_torrent_bytes(_make_torrent())
    fake.files_by_hash[infohash] = [SimpleNamespace(name="Show - 01.mkv", size=10)]
    files = await gateway.files(infohash)
    assert files == [{"name": "Show - 01.mkv", "size": 10}]


async def test_client_error_wraps_gateway_error_without_password() -> None:
    fake = FakeQbClient()
    gateway = QbittorrentGateway("h", 1, "u", SecretStr("hunter2"), client=fake)
    fake.fail_next_call = True
    with pytest.raises(GatewayError) as exc_info:
        await gateway.ping()
    assert "hunter2" not in str(exc_info.value)
    assert "RuntimeError" in str(exc_info.value)
