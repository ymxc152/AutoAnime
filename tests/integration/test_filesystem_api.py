"""目录浏览端点集成测试（P1-D）：真实 app + ASGI 传输,纯只读端点。

覆盖:空 path 盘符探测(monkeypatch os.path.exists)、正常目录列表、
404 dir_not_found、权限失败跳过(monkeypatch scandir 抛)、截断上限。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from autoanime.config import Settings
from autoanime.web.app import create_app
from autoanime.web.routers import filesystem as filesystem_module


@pytest.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    # 隔离环境变量,保证测试不依赖宿主机 AUTOANIME_* 配置。
    for key in list(os.environ):
        if key.startswith("AUTOANIME_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings()
    settings.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'fs.db').as_posix()}"
    settings.reference_enabled = False
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_empty_path_lists_windows_drives(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(filesystem_module.os, "name", "nt")
    monkeypatch.setattr(
        filesystem_module.os.path,
        "exists",
        lambda p: p in ("C:\\", "D:\\", "E:\\"),
    )
    resp = await client.get("/api/filesystem")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "path": "",
        "parent": None,
        "directories": ["C:\\", "D:\\", "E:\\"],
    }


async def test_empty_path_non_windows_returns_root(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(filesystem_module.os, "name", "posix")
    resp = await client.get("/api/filesystem")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"path": "/", "parent": None, "directories": ["/"]}


async def test_directory_listing_sorted_dirs_only(client: httpx.AsyncClient, tmp_path: Path) -> None:
    root = tmp_path / "browse"
    (root / "Beta").mkdir(parents=True)
    (root / "alpha").mkdir()
    (root / "a_file.txt").write_text("x")  # 文件不进列表
    resp = await client.get("/api/filesystem", params={"path": str(root)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["directories"] == ["alpha", "Beta"]  # casefold 排序
    assert body["path"] == str(root)
    assert body["parent"] == str(tmp_path)


async def test_missing_directory_is_404(client: httpx.AsyncClient, tmp_path: Path) -> None:
    resp = await client.get(
        "/api/filesystem", params={"path": str(tmp_path / "no_such_dir")}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "dir_not_found"


class _BrokenEntry:
    """is_dir 抛 PermissionError 的目录项（模拟单条权限失败）。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        raise PermissionError(self.name)


class _OkEntry:
    def __init__(self, name: str) -> None:
        self.name = name

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return True


def _fake_scandir_yielding(entries: list[object]):
    class _Iter:
        def __init__(self) -> None:
            self._it = iter(entries)

        def __iter__(self) -> _Iter:
            return self

        def __next__(self) -> object:
            return next(self._it)

        def close(self) -> None:
            return None

        def __enter__(self) -> _Iter:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _scandir(_path: object) -> _Iter:
        return _Iter()

    return _scandir


async def test_permission_error_entry_skipped(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        filesystem_module.os,
        "scandir",
        _fake_scandir_yielding([_OkEntry("good"), _BrokenEntry("bad"), _OkEntry("also")]),
    )
    resp = await client.get("/api/filesystem", params={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["directories"] == ["also", "good"]


async def test_scandir_total_failure_returns_empty_not_500(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raising_scandir(_path: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr(filesystem_module.os, "scandir", _raising_scandir)
    resp = await client.get("/api/filesystem", params={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["directories"] == []


async def test_listing_truncated_to_max_entries(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(filesystem_module, "_MAX_ENTRIES", 3)
    root = tmp_path / "many"
    for i in range(5):
        (root / f"dir_{i}").mkdir(parents=True)
    resp = await client.get("/api/filesystem", params={"path": str(root)})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["directories"]) == 3


async def test_drive_root_parent_is_null(client: httpx.AsyncClient, tmp_path: Path) -> None:
    # POSIX 上 tmp_path 的父即 tmp_path 的父目录,非 null;直接构造「父=自身」
    # 的根目录场景(monkeypatch is_dir 难以伪造 resolve,改走 posix 根语义验证
    # parent=null 分支已在盘符测试覆盖,这里验证真实盘符根可浏览不 500)。
    if os.name != "nt":
        pytest.skip("Windows 盘符根用例")
    resp = await client.get("/api/filesystem", params={"path": "C:\\"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "C:\\"
    assert body["parent"] is None
