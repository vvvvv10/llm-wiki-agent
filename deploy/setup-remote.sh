#!/bin/bash
# LLM Wiki Agent 服务器部署脚本（在 你的服务器IP 上执行）
set -euo pipefail

APP_DIR=/opt/llm-wiki-agent
PORT=8090
export HOME=/root
export PATH="$HOME/.local/bin:$PATH"

echo "===== 1/6 安装 uv ====="
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv --version

echo "===== 2/6 安装 Python 3.11 ====="
uv python install 3.11
uv python find 3.11

echo "===== 3/6 检查/创建 swap（内存兜底） ====="
if ! swapon --show | grep -q .; then
    if [ ! -f /swapfile ]; then
        fallocate -l 1G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
    fi
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
free -m | tail -2

echo "===== 4/6 解压代码并安装依赖 ====="
mkdir -p "$APP_DIR"
tar xzf /tmp/llm-wiki-agent.tar.gz -C "$APP_DIR"
cd "$APP_DIR"
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -i https://mirrors.aliyun.com/pypi/simple/ ".[dingtalk,docs]"
.venv/bin/python -c "import fastapi, jieba, dingtalk_stream, pypdf, openpyxl, docx; print('deps ok')"

echo "===== 5/6 写 systemd 服务 ====="
cat > /etc/systemd/system/llm-wiki-agent.service <<EOF
[Unit]
Description=LLM Wiki Agent API
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/llm-wiki-agent-worker.service <<EOF
[Unit]
Description=LLM Wiki Agent DingTalk Worker
After=network.target llm-wiki-agent.service

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
ExecStart=$APP_DIR/.venv/bin/python -m app.dingtalk.runner
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now llm-wiki-agent.service
systemctl restart llm-wiki-agent-worker.service
systemctl enable llm-wiki-agent-worker.service

echo "===== 6/6 等待启动并自检 ====="
sleep 6
systemctl is-active llm-wiki-agent.service
systemctl is-active llm-wiki-agent-worker.service
curl -s "http://127.0.0.1:$PORT/health"
echo
tail -5 /var/log/llm-wiki-agent-worker.log 2>/dev/null || journalctl -u llm-wiki-agent-worker -n 5 --no-pager
