#!/bin/bash
# systemd 服务安装与启动
set -euo pipefail

APP_DIR=/opt/llm-wiki-agent
PORT=8090

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
systemctl enable llm-wiki-agent-worker.service
systemctl restart llm-wiki-agent-worker.service
sleep 8
echo "api: $(systemctl is-active llm-wiki-agent.service)"
echo "worker: $(systemctl is-active llm-wiki-agent-worker.service)"
echo '--- health ---'
curl -s -m 10 "http://127.0.0.1:$PORT/health"
echo
echo '--- model_configured 单独确认 ---'
curl -s -m 10 "http://127.0.0.1:$PORT/health" | grep -o '"model_configured":[a-z]*'
