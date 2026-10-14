#!/bin/bash
# 启动 LLM Wiki Agent API 与 Web 页面
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[start] 未发现 .venv，正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  ./.venv/bin/pip install -e ".[dingtalk]"
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${APP_HOST:-127.0.0.1}"
PORT="${HOST_PORT:-8081}"

echo "[start] API 与 Web 页面启动中：http://localhost:${PORT}/"
exec ./.venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
