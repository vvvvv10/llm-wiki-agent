#!/bin/bash
# 启动钉钉 Stream Worker（与 API 进程独立，持有自己的一份 Wiki 索引）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[start-stream] 未发现 .venv，正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  ./.venv/bin/pip install -e ".[dingtalk]"
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# 某些 Python（尤其 python.org 安装包）未安装系统 CA，连接钉钉 WebSocket 会报
# CERTIFICATE_VERIFY_FAILED。此处用 certifi 的 CA bundle 兜底；必须在 Python
# 启动前导出，才能被 websockets/asyncio 的 TLS 握手读取。
if [ -z "${SSL_CERT_FILE:-}" ]; then
  CERTIFI_PEM="$(./.venv/bin/python - <<'PYEOF' 2>/dev/null || true
import os
import ssl

defaults = ssl.get_default_verify_paths()
if any(p and os.path.exists(p) for p in (defaults.cafile, defaults.capath)):
    raise SystemExit(0)  # 系统 CA 存在，无需兜底
try:
    import certifi

    print(certifi.where())
except ImportError:
    pass
PYEOF
)"
  if [ -n "$CERTIFI_PEM" ]; then
    export SSL_CERT_FILE="$CERTIFI_PEM"
    echo "[start-stream] 未检测到系统 CA，使用 certifi 兜底：$SSL_CERT_FILE"
  fi
fi

echo "[start-stream] 钉钉 Worker 启动中，日志：$(pwd)/stream.log"
exec ./.venv/bin/python -m app.dingtalk.runner
