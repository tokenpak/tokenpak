"""maintenance command — proxy restart, reset, log viewing."""

from __future__ import annotations

import sys
import time

from tokenpak.platform import service

PROXY_SERVICE = "tokenpak-proxy.service"


def restart_proxy() -> None:
    result = service.restart_proxy_service(PROXY_SERVICE)
    if result.ok:
        time.sleep(2)
        print(result.message)
        return
    print(result.message)
    if result.supported:
        # The platform supports a managed restart but it failed — this is an error.
        sys.exit(1)
    # Unsupported/degraded platform: actionable guidance already printed, no traceback.


def show_logs(n: int = 30) -> None:
    result = service.proxy_logs(PROXY_SERVICE, n=n)
    print(result.message)


try:
    import click

    @click.group("maintenance")
    def maintenance_cmd():
        """Proxy maintenance commands."""
        pass

    @maintenance_cmd.command("restart")
    def maintenance_restart():
        """Restart the proxy service."""
        restart_proxy()

    @maintenance_cmd.command("logs")
    @click.argument("lines", type=int, default=30, required=False)
    def maintenance_logs(lines):
        """Show last N proxy log lines."""
        show_logs(n=lines)

except ImportError:
    pass
