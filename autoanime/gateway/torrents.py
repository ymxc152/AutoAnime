"""种子文件解析与 infohash 计算（E4 下载网关的确定性底座）。

为什么自己算 infohash：qBittorrent「按 URL 添加」不返回 hash（A4 幂等
痛点），Mikan RSS enclosure 只给 .torrent 下载地址；先取回字节、本地算
出 infohash，`release_record.torrent_hash` 的唯一约束去重与补扫比对才有
确定性锚点。Bencode 编解码为纯函数（约 60 行，协议本身极简），离线单测
覆盖（含截断/畸形字节按解析失败处理，绝不抛到调用方之外）。

infohash 定义 = sha1(bencode(info dict))。解码时保持 dict 插入序与字节串
原样，重编码即逐字节还原 info 段（BGmi 同思路，MIT；实现自写）。
"""

from __future__ import annotations

import hashlib
from typing import Any


class TorrentParseError(Exception):
    """bencode 解码失败 / info 段缺失 / 类型不合法。"""


def bdecode(data: bytes) -> object:
    """解码一段 bencode 字节流；多余尾字节视为畸形。"""
    value, offset = _decode_one(data, 0)
    if offset != len(data):
        raise TorrentParseError("trailing bytes after bencode value")
    return value


def bencode(value: object) -> bytes:
    """编码为 bencode 字节流；dict 键必须为 str（编码为 UTF-8 字节序）。"""
    return _encode(value)


def _decode_one(data: bytes, pos: int) -> tuple[object, int]:
    if pos >= len(data):
        raise TorrentParseError("unexpected end of data")
    ch = data[pos : pos + 1]
    if ch == b"i":
        end = data.find(b"e", pos)
        if end < 0:
            raise TorrentParseError("unterminated integer")
        digits = data[pos + 1 : end]
        if not _valid_int_bytes(digits):
            raise TorrentParseError("invalid integer literal")
        return int(digits), end + 1
    if ch == b"l":
        pos += 1
        items: list[object] = []
        while data[pos : pos + 1] != b"e":
            value, pos = _decode_one(data, pos)
            items.append(value)
        return items, pos + 1
    if ch == b"d":
        pos += 1
        mapping: dict[str, object] = {}
        while data[pos : pos + 1] != b"e":
            key, pos = _decode_one(data, pos)
            if not isinstance(key, bytes):
                raise TorrentParseError("dict key must be a string")
            value, pos = _decode_one(data, pos)
            mapping[key.decode("utf-8", errors="surrogateescape")] = value
        return mapping, pos + 1
    colon = data.find(b":", pos)
    if colon < 0:
        raise TorrentParseError("missing length prefix")
    length_digits = data[pos:colon]
    if not _valid_int_bytes(length_digits):
        raise TorrentParseError("invalid string length")
    length = int(length_digits)
    start = colon + 1
    end = start + length
    if end > len(data):
        raise TorrentParseError("string extends past end of data")
    return data[start:end], end


def _valid_int_bytes(digits: bytes) -> bool:
    if not digits:
        return False
    if digits.startswith(b"-"):
        if len(digits) == 1 or digits[1:2] == b"0":
            return False
        return digits[1:].isdigit()
    if digits.startswith(b"0") and len(digits) > 1:
        return False
    return digits.isdigit()


def _encode(value: object) -> bytes:
    if isinstance(value, bool):
        raise TorrentParseError("bool is not a bencode type")
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="surrogateescape")
        return str(len(raw)).encode() + b":" + raw
    if isinstance(value, list):
        return b"l" + b"".join(_encode(item) for item in value) + b"e"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TorrentParseError("dict key must be a string")
            parts.append(_encode(key))
            parts.append(_encode(item))
        return b"d" + b"".join(parts) + b"e"
    raise TorrentParseError(f"unsupported bencode type: {type(value).__name__}")


def torrent_info_hash(data: bytes) -> str:
    """从 .torrent 文件字节计算 infohash（sha1 hex）。

    先整体解码验证合法性，再对 ``info`` 段做逐字节重编码哈希——解码
    保持插入序与原始字节，重编码与原文件逐字节一致。
    """
    decoded = bdecode(data)
    if not isinstance(decoded, dict):
        raise TorrentParseError("torrent root must be a dict")
    info = decoded.get("info")
    if not isinstance(info, dict):
        raise TorrentParseError("torrent missing info dict")
    return hashlib.sha1(bencode(info)).hexdigest()


def torrent_display_name(data: bytes) -> str | None:
    """种子内 ``info.name``（补扫比对用）；缺失返回 None。"""
    decoded = bdecode(data)
    if not isinstance(decoded, dict):
        return None
    info = decoded.get("info")
    if not isinstance(info, dict):
        return None
    name = info.get("name")
    if isinstance(name, bytes):
        return name.decode("utf-8", errors="replace")
    return None


def info_dict_total_length(data: bytes) -> int | None:
    """种子总大小：``info.length``（单文件）或 ``files`` 段之和（多文件）。"""
    decoded = bdecode(data)
    if not isinstance(decoded, dict):
        return None
    info = decoded.get("info")
    if not isinstance(info, dict):
        return None
    length = info.get("length")
    if isinstance(length, int) and not isinstance(length, bool):
        return length
    files = info.get("files")
    if isinstance(files, list):
        total: Any = 0
        for entry in files:
            if isinstance(entry, dict):
                file_length = entry.get("length")
                if isinstance(file_length, int) and not isinstance(file_length, bool):
                    total += file_length
        return int(total)
    return None
