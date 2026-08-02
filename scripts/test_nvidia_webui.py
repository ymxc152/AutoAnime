# -*- coding: utf-8 -*-
"""一次性脚本：验证 WebUI 数据库中的 NVIDIA 配置能通过真实 agent 代码路径完成识别。

模拟 autoanime_v3.services.scans.CoreScanAdapter._openai_config() 从 SQLite 读取配置，
再调用 OpenAIResolverAgent.resolve() 走真实 HTTP 请求。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoanime_v3.config import AppConfig
from autoanime_v3.models import MediaFile, ParsedName
from autoanime_v3.remote import OpenAIResolverAgent
from autoanime_v3.security.secrets import DpapiSecretStore
from autoanime_v3.services.auth import SecretService
from autoanime_v3.services.settings import (
    OPENAI_API_KEY_SECRET,
    OPENAI_BASE_URL_KEY,
    OPENAI_ENABLED_KEY,
    OPENAI_MODEL_KEY,
    OPENAI_TIMEOUT_KEY,
    SettingsService,
)

DATABASE = Path(__file__).resolve().parent.parent / ".dev-data" / "data" / "library.sqlite3"


def _openai_config_from_db():
    settings = SettingsService(DATABASE)
    enabled = bool(settings.get(OPENAI_ENABLED_KEY, False))
    base_url = str(settings.get(OPENAI_BASE_URL_KEY, "https://api.openai.com"))
    model = str(settings.get(OPENAI_MODEL_KEY, "gpt-4.1-mini"))
    try:
        timeout = max(5, int(settings.get(OPENAI_TIMEOUT_KEY, 30)))
    except (TypeError, ValueError):
        timeout = 30
    api_key = ""
    if enabled:
        try:
            store = DpapiSecretStore()
        except OSError:
            store = None
        if store is not None:
            api_key = SecretService(DATABASE, store).reveal_for_integration(OPENAI_API_KEY_SECRET) or ""
    return {
        "openai_enabled": bool(enabled and api_key),
        "openai_base_url": base_url,
        "openai_model": model,
        "openai_api_key": api_key,
        "openai_timeout": timeout,
    }


def main():
    cfg = _openai_config_from_db()
    print("=== WebUI 数据库配置生效检查 ===")
    print("enabled    =", cfg["openai_enabled"])
    print("base_url   =", cfg["openai_base_url"])
    print("model      =", cfg["openai_model"])
    print("timeout    =", cfg["openai_timeout"])
    print("api_key    =", (cfg["openai_api_key"] or "")[:8], "(configured)" if cfg["openai_api_key"] else "(MISSING)")
    assert cfg["openai_enabled"] is True, "openai.enabled 未生效"
    assert cfg["openai_api_key"].startswith("sk-"), "API key 未生效"
    assert "api.ymxc.asia" in cfg["openai_base_url"], "base_url 未生效"
    assert "deepseek-v4-flash" in cfg["openai_model"], "model 未生效"

    config = AppConfig(
        database_path=DATABASE,
        alias_file=Path(__file__).resolve().parent.parent / "autoanime_v3" / "data" / "aliases.json",
        openai_enabled=cfg["openai_enabled"],
        openai_base_url=cfg["openai_base_url"],
        openai_model=cfg["openai_model"],
        openai_api_key=cfg["openai_api_key"],
        openai_timeout=cfg["openai_timeout"],
    )
    agent = OpenAIResolverAgent(config)
    print("\n=== 调用 OpenAIResolverAgent.resolve() ===")
    media = MediaFile(
        path=Path("[Subbers] 鬼灭之刃 无限列车篇 第01话 [1080p].mkv"),
        input_root=Path("."),
        context_name="鬼灭之刃",
        relative_path="[Subbers] 鬼灭之刃 无限列车篇 第01话 [1080p].mkv",
        size=0,
        mtime_ns=0,
    )
    parsed = ParsedName(
        raw_title="鬼灭之刃 无限列车篇",
        season=None,
        episode=None,
        title_candidates=("鬼灭之刃 无限列车篇",),
    )
    result = agent.resolve(media, parsed)
    if result is None:
        print("agent.resolve 返回 None（HTTP 失败或解析失败）")
        sys.exit(1)
    print("识别成功 =>", result)
    assert result["title"] and "鬼灭之刃" in result["title"], "title 未收敛到鬼灭之刃"
    assert result["episode"] >= 1, "episode 非法"
    print("\n=== WebUI NVIDIA 端到端测试通过 ===")


if __name__ == "__main__":
    main()
