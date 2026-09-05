# AutoAnime v2 backend（E4，D16 单进程：FastAPI + AsyncIOScheduler）
# uv 官方镜像（astral-sh/uv）自带 uv + Python 3.12；容器内无系统时区库，
# tzdata 已在依赖里（D20 JST 判定跨平台一致）。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 依赖层先行（利用层缓存）：先拷 pyproject/uv.lock 装 deps，再拷代码
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY autoanime ./autoanime
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# 数据与媒体目录（实际由 compose 挂载覆盖）
RUN mkdir -p /data/library /data/downloads /data/quarantine

EXPOSE 8000

# D16：一个进程同时承载 FastAPI + AsyncIOScheduler（含通知泵与启动对账）
CMD ["uv", "run", "--no-dev", "uvicorn", "autoanime.scheduler.asgi:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
