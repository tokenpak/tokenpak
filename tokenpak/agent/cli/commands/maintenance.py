"""maintenance command — proxy restart, reset, log viewing."""

from __future__ import annotations

import subprocess
import sys
import time

PROXY_SERVICE = "tokenpak-proxy.service"


def restart_proxy() -> None:
    try:
        subprocess.run(["systemctl", "--user", "restart", PROXY_SERVICE], check=True)
        time.sleep(2)
        print("✓ Proxy service restarted")
    except subprocess.CalledProcessError as e:
        print(f"✖ Restart failed: {e}")
        sys.exit(1)


def show_logs(n: int = 30) -> None:
    r = subprocess.run(
        ["journalctl", "--user", "-u", PROXY_SERVICE, f"-n{n}", "--no-pager"],
        capture_output=True,
        text=True,
    )
    print(r.stdout or r.stderr)


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
