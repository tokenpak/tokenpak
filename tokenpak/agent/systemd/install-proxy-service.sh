#!/usr/bin/env bash
# Install tokenpak-proxy as a systemd user service (auto-restarts on crash)
# Usage: ./install-proxy-service.sh
set -e

SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="tokenpak-proxy"
TOKENPAK_BIN=$(which tokenpak 2>/dev/null || echo "tokenpak")

mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/${SERVICE_NAME}.service" << EOF
[Unit]
Description=TokenPak LLM Proxy (Graceful Degradation)
Documentation=https://docs.tokenpak.dev/proxy
After=network.target

[Service]
Type=simple
ExecStart=${TOKENPAK_BIN} serve
Restart=on-failure
RestartSec=5s
StartLimitIntervalSec=60
StartLimitBurst=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tokenpak-proxy
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service"
systemctl --user start "${SERVICE_NAME}.service"

echo "✅ tokenpak-proxy service installed and started."
echo ""
echo "  Status: systemctl --user status ${SERVICE_NAME}"
echo "  Logs:   journalctl --user -u ${SERVICE_NAME} -f"
echo "  Stop:   systemctl --user stop ${SERVICE_NAME}"
echo ""
echo "To add API keys, create ~/.tokenpak/env:"
echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.tokenpak/env"
echo "Then add 'EnvironmentFile=%h/.tokenpak/env' to the [Service] section"
echo "and run: systemctl --user daemon-reload && systemctl --user restart ${SERVICE_NAME}"
