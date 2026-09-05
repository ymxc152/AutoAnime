"""gateway.torrents 单测：bencode 解码/编码 + infohash 确定性（E4a）。

infohash 期望值构造为**非循环**：手工书写 .torrent 字节，期望哈希 =
sha1(原始字节流中 info 段的精确子串)——不经任何解码/重编码路径。
"""

from __future__ import annotations

import hashlib

import pytest

from autoanime.gateway.torrents import (
    TorrentParseError,
    bdecode,
    bencode,
    info_dict_total_length,
    torrent_display_name,
    torrent_info_hash,
)

# 手工构造：d4:infod4:name8:Test.mkv6:lengthi5ee e
# info 段精确字节 = "d4:name8:Test.mkv6:lengthi5ee"
INFO_SLICE = b"d4:name8:Test.mkv6:lengthi5ee"
TORRENT = b"d4:info" + INFO_SLICE + b"e"


def test_infohash_matches_raw_slice_sha1() -> None:
    expected = hashlib.sha1(INFO_SLICE).hexdigest()
    assert torrent_info_hash(TORRENT) == expected


def test_infohash_is_deterministic_and_hex40() -> None:
    first = torrent_info_hash(TORRENT)
    assert first == torrent_info_hash(TORRENT)
    assert len(first) == 40
    int(first, 16)  # 全 hex


def test_infohash_multi_file_total_length() -> None:
    multi = (
        b"d4:infod5:filesl"
        b"d6:lengthi3e4:pathl4:a.mkee"
        b"d6:lengthi2e4:pathl4:b.mkee"
        b"eee"
    )
    assert info_dict_total_length(multi) == 5
    single = b"d4:infod6:lengthi7eee"
    assert info_dict_total_length(single) == 7


def test_display_name() -> None:
    assert torrent_display_name(TORRENT) == "Test.mkv"
    assert torrent_display_name(b"d3:foo3:bare") is None


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"i42e", 42),
        (b"i-7e", -7),
        (b"4:spam", b"spam"),
        (b"l4:spami1ee", [b"spam", 1]),
        (b"d3:bari1e3:foo4:spame", {"bar": 1, "foo": b"spam"}),
        (b"", None),
    ],
)
def test_bdecode_roundtrip(data: bytes, expected: object) -> None:
    if data == b"":
        with pytest.raises(TorrentParseError):
            bdecode(data)
        return
    assert bdecode(data) == expected
    assert bencode(bdecode(data)) == data


@pytest.mark.parametrize(
    "data",
    [
        b"i4",  # 未闭合整数
        b"i04e",  # 前导零
        b"i-0e",  # 负零
        b"5:spa",  # 字符串越界
        b"9:spam",  # 长度不匹配
        b"l4:spam",  # 未闭合列表
        b"d3:foo",  # 未闭合 dict
        b"d4:infoi1eee x",  # 尾部杂字节
        b"li1ei0",  # 深层截断
    ],
)
def test_bdecode_malformed(data: bytes) -> None:
    with pytest.raises(TorrentParseError):
        bdecode(data)


def test_bencode_rejects_non_string_dict_keys() -> None:
    with pytest.raises(TorrentParseError):
        bencode({1: b"x"})


def test_bencode_rejects_bool() -> None:
    with pytest.raises(TorrentParseError):
        bencode(True)


def test_infohash_missing_info_dict() -> None:
    with pytest.raises(TorrentParseError):
        torrent_info_hash(b"d3:foo4:bare")
