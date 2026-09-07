"""12-D 配置中心单测：app_settings 存储语义 + scheduler loop 重建收口。

全部离线：store 走内存 SQLite；重建路径以 fake scheduler/build_loop 断言
「先 build 新 loop 成功再 tear down 旧 loop」的失败收口（对齐 run-once
互斥锁 finally 释放纪律）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from autoanime.config import Settings
from autoanime.memory.store import SqliteStorage
from autoanime.scheduler.scheduler import rebuild_running_scheduler

# ---------------------------------------------------------------------------
# SqliteStorage.app_settings（12-D 存储侧）
# ---------------------------------------------------------------------------


async def test_app_settings_roundtrip_and_clear() -> None:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    try:
        assert await store.list_app_settings() == {}
        await store.put_app_setting("dry_run", "false")
        await store.put_app_setting("rss_poll_interval_minutes", "15")
        assert await store.list_app_settings() == {
            "dry_run": "false",
            "rss_poll_interval_minutes": "15",
        }
        # 同 key 再写 = 覆盖（每 key 至多一行）
        await store.put_app_setting("dry_run", "true")
        assert (await store.list_app_settings())["dry_run"] == "true"
        # value=None = 清除（删行，回落 env/toml 默认）；重复清除幂等
        await store.put_app_setting("dry_run", None)
        await store.put_app_setting("dry_run", None)
        assert await store.list_app_settings() == {"rss_poll_interval_minutes": "15"}
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# rebuild_running_scheduler（12-D 调度类 PUT 的重建钩子）
# ---------------------------------------------------------------------------


class _FakeComponents:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeOldScheduler:
    """被替换的现役调度（只验证 shutdown 被调、状态翻转）。"""

    def __init__(self) -> None:
        self.running = True
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.running = False


class _FakeNewScheduler:
    """SubscriptionScheduler 的替身（避免单测里真起 AsyncIOScheduler）。"""

    def __init__(self, components: Any, settings: Settings) -> None:
        self.components = components
        self.settings = settings
        self.start_calls = 0

    @property
    def running(self) -> bool:
        return self.start_calls > 0

    def start(self) -> None:
        self.start_calls += 1


@pytest.fixture
def fake_scheduler_module(monkeypatch: pytest.MonkeyPatch) -> list[_FakeNewScheduler]:
    created: list[_FakeNewScheduler] = []

    def _factory(components: Any, settings: Settings) -> _FakeNewScheduler:
        instance = _FakeNewScheduler(components, settings)
        created.append(instance)
        return instance

    monkeypatch.setattr("autoanime.scheduler.scheduler.SubscriptionScheduler", _factory)
    return created


def _state(
    scheduler: Any = None, components: Any = None
) -> SimpleNamespace:
    return SimpleNamespace(
        scheduler=scheduler,
        loop_components=components,
        storage=None,
        bus=None,
    )


async def test_rebuild_replaces_running_loop(
    monkeypatch: pytest.MonkeyPatch, fake_scheduler_module: list[_FakeNewScheduler]
) -> None:
    old_components = _FakeComponents()
    old_scheduler = _FakeOldScheduler()
    state = _state(old_scheduler, old_components)
    new_components = _FakeComponents()
    build_calls: list[dict[str, Any]] = []

    def fake_build(settings: Settings, **kwargs: Any) -> _FakeComponents:
        build_calls.append(kwargs)
        return new_components

    monkeypatch.setattr("autoanime.scheduler.scheduler.build_loop", fake_build)

    warnings = await rebuild_running_scheduler(state, Settings())

    assert warnings == []
    assert len(build_calls) == 1
    # 旧 loop 已停、新 loop 已挂上 state 并启动（默认 scheduler_enabled=True）
    assert old_scheduler.shutdown_calls == 1
    assert old_components.closed is True
    assert state.scheduler is fake_scheduler_module[0]
    assert state.scheduler.components is new_components
    assert state.scheduler.start_calls == 1
    assert state.loop_components is new_components


async def test_rebuild_respects_scheduler_disabled(
    monkeypatch: pytest.MonkeyPatch, fake_scheduler_module: list[_FakeNewScheduler]
) -> None:
    old_scheduler = _FakeOldScheduler()
    state = _state(old_scheduler, _FakeComponents())
    monkeypatch.setattr(
        "autoanime.scheduler.scheduler.build_loop",
        lambda settings, **kwargs: _FakeComponents(),
    )

    warnings = await rebuild_running_scheduler(state, Settings(scheduler_enabled=False))

    assert warnings == []
    assert fake_scheduler_module[0].start_calls == 0


async def test_rebuild_failure_keeps_old_loop(
    monkeypatch: pytest.MonkeyPatch, fake_scheduler_module: list[_FakeNewScheduler]
) -> None:
    old_components = _FakeComponents()
    old_scheduler = _FakeOldScheduler()
    state = _state(old_scheduler, old_components)

    def failing_build(settings: Settings, **kwargs: Any) -> Any:
        raise RuntimeError("loop bootstrap failed")

    monkeypatch.setattr("autoanime.scheduler.scheduler.build_loop", failing_build)

    warnings = await rebuild_running_scheduler(state, Settings())

    # 构建失败：旧 loop 保持运行（不制造半拆状态），PUT 响应带警告
    assert warnings == ["scheduler rebuild failed; old loop kept running"]
    assert old_scheduler.shutdown_calls == 0
    assert old_components.closed is False
    assert state.scheduler is old_scheduler
    assert fake_scheduler_module == []


async def test_rebuild_noop_without_scheduler() -> None:
    # API-only 进程（python -m autoanime.api serve）无 scheduler：只落库。
    state = _state()
    assert await rebuild_running_scheduler(state, Settings()) == []
    assert getattr(state, "scheduler", None) is None
