"""compose 结构自检（E4b，D10 本机无 docker 的替代验收；任务提示词第 7 项）。

- docker-compose.yml 可被 YAML 解析；服务/挂载/env 与计划一致（B8 同盘）；
- .env.example 全部为占位空值（无真实密钥）；nginx 反代含 SSE 配置；
- docs/DEPLOY.md 含三条纪律文案。
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def _load_compose() -> dict:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    return parsed


def test_compose_has_backend_and_frontend_services() -> None:
    compose = _load_compose()
    services = compose["services"]
    assert set(services) == {"backend", "frontend"}


def test_backend_mounts_share_same_disk_layout() -> None:
    """B8：库与下载目录同挂 /data 下（同盘），hardlink 不降级。"""
    compose = _load_compose()
    backend = compose["services"]["backend"]
    volumes = [entry for entry in backend["volumes"]]
    assert "./data:/data" in volumes
    assert "./library:/data/library" in volumes
    assert "./downloads:/data/downloads" in volumes
    env = backend["environment"]
    joined = "\n".join(env)
    assert "AUTOANIME_LIBRARY_PATH=/data/library" in joined
    assert "AUTOANIME_DOWNLOAD_PATH=/data/downloads" in joined
    assert "AUTOANIME_QUARANTINE_PATH=/data/quarantine" in joined


def test_backend_command_runs_scheduler_enabled_app() -> None:
    compose = _load_compose()
    backend = compose["services"]["backend"]
    assert backend["build"]["dockerfile"] == "docker/backend.Dockerfile"
    dockerfile = (ROOT / "docker/backend.Dockerfile").read_text(encoding="utf-8")
    assert "autoanime.scheduler.asgi:create_app" in dockerfile  # D16 工厂
    assert "--factory" in dockerfile


def test_frontend_proxies_api_with_sse_config() -> None:
    compose = _load_compose()
    frontend = compose["services"]["frontend"]
    assert frontend["build"]["dockerfile"] == "docker/frontend.Dockerfile"
    assert frontend["depends_on"] == ["backend"]
    nginx = (ROOT / "docker/nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://backend:8000" in nginx
    assert "proxy_buffering off" in nginx  # SSE 不缓冲
    assert "location /api/" in nginx


def test_env_example_has_no_real_secrets() -> None:
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for required in [
        "AUTOANIME_API_TOKEN=",
        "AUTOANIME_QBITTORRENT_PASSWORD=",
        "AUTOANIME_LLM_API_KEY=",
        "AUTOANIME_NOTIFY_WEBHOOK_URL=",
        "AUTOANIME_NOTIFY_TELEGRAM_BOT_TOKEN=",
    ]:
        assert required in env_text
    # 密钥行全部为空占位（= 后无值）
    for line in env_text.splitlines():
        if any(key in line for key in ("TOKEN=", "PASSWORD=", "API_KEY=", "SECRET=", "WEBHOOK_URL=")):
            assert line.endswith("="), f"env.example 出现非空密钥：{line}"


def test_deploy_doc_carries_three_disciplines() -> None:
    deploy = (ROOT / "docs/DEPLOY.md").read_text(encoding="utf-8")
    assert "每番只订一个字幕组" in deploy
    assert "勿暴露公网" in deploy
    assert "同一盘" in deploy  # B8 同盘说明
