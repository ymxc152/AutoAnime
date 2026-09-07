"""12-D 配置中心单测：app_settings 存储语义 + scheduler loop 重建收口。

全部离线：store 走内存 SQLite；重建路径以 fake scheduler/build_loop 断言
「先 build 新 loop 成功再 tear down 旧 loop」的失败收口（对齐 run-once
互斥锁 finally 释放纪律）。
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast

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


# ---------------------------------------------------------------------------
# update_settings（12-D 修复：immediate 装配固化字段 / log_level / null 清除）
# ---------------------------------------------------------------------------


@pytest.fixture
async def settings_store() -> Any:
    store = SqliteStorage("sqlite+aiosqlite:///:memory:")
    await store.create_all()
    try:
        yield store
    finally:
        await store.close()


def _router_state(
    store: SqliteStorage, reference_chain: Any = None
) -> SimpleNamespace:
    """仿 request.app.state：无 scheduler（API-only 语义，rebuild no-op）。"""
    return SimpleNamespace(
        reference_chain=reference_chain,
        scheduler=None,
        loop_components=None,
        storage=store,
        bus=None,
    )


async def _call_update(
    settings: Settings,
    store: SqliteStorage,
    payload: dict[str, Any],
    *,
    state: SimpleNamespace | None = None,
) -> Any:
    from autoanime.memory.governance import MemoryGovernance
    from autoanime.web.routers.settings import update_settings
    from autoanime.web.schemas import SettingsUpdateIn

    state = state or _router_state(store)
    # SimpleNamespace 模拟 starlette Request（update_settings 只读 request.app.state）。
    request = cast(Any, SimpleNamespace(app=SimpleNamespace(state=state)))
    return await update_settings(
        SettingsUpdateIn(**payload),
        request,
        settings,
        store,
        MemoryGovernance(store),
    )


async def test_reference_field_put_rebuilds_reference_chain(
    settings_store: SqliteStorage,
) -> None:
    # 缺陷 1：reference 字段仅 setattr 不够——ReferenceChain 构造即冻结
    # order/enabled；PUT 后须重建并重赋 app.state.reference_chain。
    settings = Settings(reference_enabled=False)
    state = _router_state(settings_store, reference_chain=None)
    out = await _call_update(
        settings, settings_store, {"reference_enabled": True}, state=state
    )
    assert out.applied["reference_enabled"] == "immediate"
    assert settings.reference_enabled is True
    # 重建后 app.state.reference_chain 挂上新链（reference_enabled=True → 非 None）
    assert state.reference_chain is not None


async def test_orchestrator_field_put_triggers_scheduler_rebuild(
    settings_store: SqliteStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 缺陷 1：orchestrator/recognizer 装配期固化字段（llm_model 等）PUT 后
    # 须触发 scheduler loop 重建（与 _SCHEDULER_FIELDS 同路），使 loop 内
    # 用新 Settings 重组。
    calls: list[tuple[Any, Settings]] = []

    async def fake_rebuild(state: Any, settings: Settings) -> list[str]:
        calls.append((state, settings))
        return []

    monkeypatch.setattr(
        "autoanime.web.routers.settings.rebuild_running_scheduler", fake_rebuild
    )
    settings = Settings()
    state = _router_state(settings_store)
    out = await _call_update(
        settings, settings_store, {"llm_model": "test-model"}, state=state
    )
    assert out.applied["llm_model"] == "immediate"
    assert settings.llm_model == "test-model"
    assert len(calls) == 1

    # 非装配固化字段（dry_run 运行期动态读取）不触发重建
    calls.clear()
    await _call_update(settings, settings_store, {"dry_run": True}, state=state)
    assert calls == []


async def test_immediate_nullable_field_null_clears_override(
    settings_store: SqliteStorage,
) -> None:
    # 缺陷 3：非密钥可空字段（immediate 档）显式 null = 清除覆盖项——
    # 删 DB 行 + 运行时回落 None（对齐密钥 null 清除）。
    await settings_store.put_app_setting("llm_model", '"old-model"')
    settings = Settings(llm_model="old-model")
    state = _router_state(settings_store)
    out = await _call_update(settings, settings_store, {"llm_model": None}, state=state)
    assert out.applied["llm_model"] == "immediate"
    assert settings.llm_model is None
    assert await settings_store.list_app_settings() == {}


async def test_restart_nullable_field_null_clears_override(
    settings_store: SqliteStorage,
) -> None:
    # 缺陷 3：restart 档可空字段（llm_base_url）显式 null 清除 DB 行；
    # 运行时同步回落 None 使 GET 立即显示「无覆盖」（消费方已固化不受影响）。
    await settings_store.put_app_setting("llm_base_url", '"http://example.invalid/v1"')
    settings = Settings(llm_base_url="http://example.invalid/v1")
    out = await _call_update(settings, settings_store, {"llm_base_url": None})
    assert out.applied["llm_base_url"] == "requires_restart"
    assert settings.llm_base_url is None
    assert await settings_store.list_app_settings() == {}


async def test_log_level_put_reconfigures_runtime_logging(
    settings_store: SqliteStorage,
) -> None:
    # 缺陷 2：log_level PUT 对运行进程做 logging 重配置（root + uvicorn
    # loggers），而非仅改 Settings 值。
    root = logging.getLogger()
    uvicorn_err = logging.getLogger("uvicorn.error")
    old_root, old_err = root.level, uvicorn_err.level
    root.setLevel(logging.WARNING)
    uvicorn_err.setLevel(logging.WARNING)
    try:
        settings = Settings()
        out = await _call_update(settings, settings_store, {"log_level": "DEBUG"})
        assert out.applied["log_level"] == "immediate"
        assert settings.log_level == "DEBUG"
        assert root.level == logging.DEBUG
        assert uvicorn_err.level == logging.DEBUG
        # 无效级别：只警告不改现有级别，不使 PUT 失败
        await _call_update(settings, settings_store, {"log_level": "NOPE"})
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(old_root)
        uvicorn_err.setLevel(old_err)
