#!/bin/bash
if ! curl -sf --max-time 5 http://localhost:8766/health >/dev/null 2>&1; then
    echo "[$(date)] Proxy down — restarting" >> ~/tokenpak/watchdog.log
    systemctl --user restart tokenpak-proxy
fi
