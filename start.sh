#!/usr/bin/env bash
# AutoAnime 一键启动（Linux / macOS）：依赖安装 + 建库 + 后端/前端 + 打开浏览器
# Ctrl+C 会同时停掉后端与前端。
set -euo pipefail
cd "$(dirname "$0")"

echo "================================================"
echo "  AutoAnime 启动器"
echo "================================================"

command -v uv >/dev/null 2>&1 || { echo "[错误] 未找到 uv，请先安装：https://docs.astral.sh/uv/"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "[错误] 未找到 Node.js（需 >= 20），请先安装：https://nodejs.org/"; exit 1; }

if [ ! -f .env ]; then
    echo "[初始化] 从 .env.example 生成 .env，密钥请自行填写..."
    cp .env.example .env
fi

echo "[检查] Python 依赖（uv sync，已就绪时秒过）..."
uv sync

if [ ! -d frontend/node_modules ]; then
    echo "[首次运行] 安装前端依赖（npm install，只需一次）..."
    (cd frontend && npm install)
fi

echo "[检查] 数据库（幂等，可重复执行）..."
uv run autoanime init-db

echo "[启动] 后端 API（默认 127.0.0.1:8000）..."
uv run python -m autoanime.api serve --dev &
BACKEND_PID=$!

echo "[启动] 前端 WebUI（默认 http://localhost:5173，已连真后端）..."
(cd frontend && VITE_USE_MOCK=0 npm run dev) &
FRONTEND_PID=$!

trap 'kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true' EXIT INT TERM

echo "[等待] 前端就绪后自动打开浏览器（最多 60 秒）..."
FRONTEND_URL="http://localhost:5173"
for _ in $(seq 1 60); do
    curl -sf -o /dev/null "http://127.0.0.1:5173" && { READY=1; break; }
    sleep 1
done

if [ "${READY:-0}" = "1" ]; then
    echo "[完成] WebUI 已就绪：$FRONTEND_URL"
    if command -v xdg-open >/dev/null 2>&1; then xdg-open "$FRONTEND_URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then open "$FRONTEND_URL" >/dev/null 2>&1 || true
    else echo "请手动打开 $FRONTEND_URL"; fi
else
    echo "[警告] 60 秒内前端未就绪，请查看上方日志；也可手动打开 $FRONTEND_URL"
fi

echo "按 Ctrl+C 停止后端与前端。"
wait
