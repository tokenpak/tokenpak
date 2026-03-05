#!/usr/bin/env bash
# Install tokenpak-watcher as a systemd user service
# Usage: ./install-service.sh [/path/to/watch]
set -e

WATCH_PATH="${1:-$HOME}"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="tokenpak-watcher"

mkdir -p "$SERVICE_DIR"

# Encode the path for systemd instance naming
ENCODED_PATH=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$WATCH_PATH")

# Write the service file
cat > "$SERVICE_DIR/${SERVICE_NAME}.service" << EOF
[Unit]
Description=TokenPak Vault File Watcher
After=network.target

[Service]
Type=simple
ExecStart=$(which tokenpak) index ${WATCH_PATH} --watch
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tokenpak-watcher
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service"
systemctl --user start "${SERVICE_NAME}.service"

echo "Service installed and started."
echo "  Status: systemctl --user status ${SERVICE_NAME}"
echo "  Logs:   journalctl --user -u ${SERVICE_NAME} -f"
