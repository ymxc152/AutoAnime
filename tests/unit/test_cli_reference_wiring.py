"""PR7 M2b：CLI confirm 侧 ``reference_lookup`` 装配（alias 回填钩子接线）。

覆盖：confirm 调 ``learn_confirmation`` 时传入与 parse 管线同源的参考链
（Registry 注册 + ``CachedReference`` 包装 + confirm 侧 storage 作缓存库），
且回填钩子真的经链触达 provider；``reference_enabled=False`` 时传 ``None``
且不注册任何 provider（与 M3 之前的 CLI 行为逐字节一致）。全部离线 fake。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from autoanime import cli
from autoanime.config import Settings
from autoanime.core.interfaces import MetadataReference
from autoanime.memory.learn import LearnOutcome
from autoanime.memory.reference_cache import CachedReference
from autoanime.memory.store import SqliteStorage
from autoanime.pipeline.l2.placeholders import build_title_shape
from autoanime.pipeline.l3 import ReferenceChain, ReferenceFacts

QUERY_TITLE = "葬送的芙莉莲"
CANONICAL_TITLE = "葬送のフリーレン"
QUERY_SHAPE = build_title_shape(QUERY_TITLE)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "l2_enabled": True,
        "llm_enabled": False,
        "reference_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _confirm_args() -> argparse.Namespace:
    return argparse.Namespace(
        command="confirm",
        name="[Sub] Frieren - 01.mkv",
        title=QUERY_TITLE,
        season=1,
        episode=1,
        segment=None,
        fansub=None,
        source="manual",
    )


class RecordingProvider:
    """计数 fake 参考源：返回预置 canonical facts。"""

    def __init__(self) -> None:
        self.shapes: list[str] = []

    async def lookup(self, title_shape: str) -> ReferenceFacts | None:
        self.shapes.append(title_shape)
        return ReferenceFacts(canonical_title=CANONICAL_TITLE, source="bangumi")


@pytest.fixture
def captured_learn(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """把 ``learn_confirmation`` 换成捕获 kwargs 的 fake。"""
    captured: dict[str, object] = {}

    async def fake_learn(store: object, **kwargs: object) -> LearnOutcome:
        captured.update(kwargs)
        return LearnOutcome(entries=(), bypassed=False)

    monkeypatch.setattr(cli, "learn_confirmation", fake_learn)
    return captured


@pytest.fixture
def wired_providers(monkeypatch: pytest.MonkeyPatch):
    """把 ``register_reference_providers`` 换成注册 fake 的生产同款装配：
    每实例 ``CachedReference`` 包装、confirm 侧 storage 作缓存库。"""
    providers: list[RecordingProvider] = []
    registered: dict[str, object] = {}

    def fake_register(
        registry: object,
        *,
        cache_store: object = None,
        reference_qps: float | None = None,
    ) -> None:
        registered["cache_store"] = cache_store
        registered["reference_qps"] = reference_qps
        provider = RecordingProvider()
        providers.append(provider)
        assert isinstance(cache_store, SqliteStorage)
        wrapped = CachedReference(
            provider="bangumi", upstream=provider, store=cache_store
        )
        registry.register(MetadataReference, "bangumi")(wrapped)  # type: ignore[attr-defined]

    monkeypatch.setattr(cli, "register_reference_providers", fake_register)
    return providers, registered


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


async def _bootstrap_db(url: str) -> None:
    """Pre-create the schema so CachedReference can persist cache rows."""
    bootstrap = SqliteStorage(url)
    try:
        await bootstrap.create_all()
    finally:
        await bootstrap.close()


async def test_confirm_passes_wired_reference_lookup_and_backfill_reaches_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    captured_learn: dict[str, object], wired_providers,
) -> None:
    providers, registered = wired_providers
    url = f"sqlite+aiosqlite:///{(tmp_path / 'confirm.db').as_posix()}"
    await _bootstrap_db(url)
    _patch_settings(monkeypatch, _settings(database_url=url))

    rc = await cli._confirm(_confirm_args())

    assert rc == 0
    # confirm 把装配好的链作为 reference_lookup 传给了 learn_confirmation
    lookup = captured_learn["reference_lookup"]
    assert isinstance(lookup, ReferenceChain)
    assert lookup.names == ("bangumi",)
    assert registered["cache_store"] is not None
    # 回填钩子真的经链触达 provider：链查询命中 fake 参考源（零真实网络），
    # 且查询 shape 是 confirmed 标题的归一形状
    facts = await lookup.lookup(QUERY_SHAPE)
    assert facts is not None
    assert facts.canonical_title == CANONICAL_TITLE
    assert providers[0].shapes == [QUERY_SHAPE]


async def test_confirm_reference_lookup_shares_reference_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    captured_learn: dict[str, object], wired_providers,
) -> None:
    """生产同款 ``CachedReference`` 包装生效：同 shape 二次查询零 provider 外呼。"""
    providers, _registered = wired_providers
    url = f"sqlite+aiosqlite:///{(tmp_path / 'confirm.db').as_posix()}"
    await _bootstrap_db(url)
    _patch_settings(monkeypatch, _settings(database_url=url))

    assert await cli._confirm(_confirm_args()) == 0
    lookup = captured_learn["reference_lookup"]
    assert isinstance(lookup, ReferenceChain)
    assert await lookup.lookup(QUERY_SHAPE) is not None

    assert len(providers[0].shapes) == 1


async def test_confirm_reference_disabled_passes_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    captured_learn: dict[str, object],
) -> None:
    def fail_register(*args: object, **kwargs: object) -> None:
        raise AssertionError("providers must not be registered when reference disabled")

    monkeypatch.setattr(cli, "register_reference_providers", fail_register)
    _patch_settings(
        monkeypatch,
        _settings(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'confirm.db').as_posix()}",
            reference_enabled=False,
        ),
    )

    rc = await cli._confirm(_confirm_args())

    assert rc == 0
    assert captured_learn["reference_lookup"] is None


async def test_confirm_reference_lookup_helper_builds_cached_chain(tmp_path: Path) -> None:
    """直接装配点：真实 register_reference_providers 离线构造（adapter 懒创建）。"""
    settings = _settings(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'confirm.db').as_posix()}")
    async with SqliteStorage(settings.database_url) as storage:
        chain = cli._confirm_reference_lookup(settings, storage)
        assert chain is not None
        assert chain.names == ("bangumi", "tmdb")
