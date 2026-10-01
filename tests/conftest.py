"""全局测试隔离（启用审计发现）：宿主机项目目录下可能存在真实 .env
（AUTOANIME_TMDB_API_KEY 等），env_file 加载后会让"密钥未配置"类断言
依赖宿主机状态。此处统一把 Settings 的 env_file 置空——测试一律显式
注入所需配置，与宿主环境解耦；进程环境变量仍可用（monkeypatch 可控）。
"""

from __future__ import annotations

import pytest

from autoanime.config import Settings, SettingsConfigDict


@pytest.fixture(autouse=True)
def _isolate_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Settings,
        "model_config",
        SettingsConfigDict(env_prefix="AUTOANIME_", extra="ignore"),
    )
