"""
tokenpak dashboard — Real-time TUI health dashboard.

Phase 3 Deliverable 5.

Shows:
  - Proxy status (running/stopped, uptime)
  - Request stats for the last hour
  - Compression stats
  - Auth profile status
  - Recent errors

Refreshes every 5 seconds. Press 'q' or Ctrl-C to quit.

Usage:
    tokenpak dashboard              # local TUI
    tokenpak dashboard --fleet      # fleet-wide summary
    tokenpak dashboard --json       # JSON export (non-interactive)
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PROXY_PORT = int(os.environ.get("TOKENPAK_PORT", "8766"))
AUTH_PROFILES_FILE = Path.home() / ".tokenpak" / "auth-profiles.json"
FLEET_CONFIG_FILE = Path.home() / ".tokenpak" / "fleet.yaml"
REFRESH_INTERVAL = 5  # seconds


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

def _http_get(path: str, port: int = PROXY_PORT, timeout: float = 3.0) -> Optional[Dict]:
    """Fetch JSON from proxy management endpoint. Returns None on failure."""
    try:
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _proxy_start_time() -> Optional[float]:
    """Estimate proxy start time from PID file or process info."""
    pid_file = Path.home() / ".tokenpak" / "proxy.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            stat_file = Path(f"/proc/{pid}/stat")
            if stat_file.exists():
                return stat_file.stat().st_mtime
        except Exception:
            pass
    return None


def _uptime_str(start_time: Optional[float]) -> str:
    if not start_time:
        return "unknown"
    elapsed = int(time.time() - start_time)
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def _load_auth_profiles() -> Dict[str, Any]:
    if not AUTH_PROFILES_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_PROFILES_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _profile_status(profile: Dict[str, Any]) -> tuple[str, str]:
    """Return (icon, description) for a profile."""
    now = time.time()
    expires_at = profile.get("expires_at", 0)
    cooldown_until = profile.get("cooldownUntil", 0)

    if cooldown_until and cooldown_until > now:
        remaining = int(cooldown_until - now)
        return "⚠", f"in cooldown ({remaining}s remaining)"
    if expires_at:
        remaining = expires_at - now
        if remaining < 0:
            return "❌", "expired — run: tokenpak auth login"
        h = remaining / 3600
        return "✓", f"valid ({h:.0f}h left)"
    return "✓", "active (no expiry)"


def collect_local_data() -> Dict[str, Any]:
    """Gather all data for the local dashboard view."""
    health = _http_get("/health")
    stats = _http_get("/stats")
    stats_session = _http_get("/stats/session")
    degradation = _http_get("/degradation")

    proxy_running = health is not None and health.get("status") in ("ok", "degraded")

    # Request stats (best-effort from /stats or /stats/session)
    req_data = stats or stats_session or {}
    requests = req_data.get("requests", req_data.get("total_requests", 0))
    errors = req_data.get("errors", req_data.get("total_errors", 0))
    avg_latency_ms = req_data.get("avg_latency_ms", req_data.get("latency_avg_ms", 0))
    tokens_in = req_data.get("tokens_in", req_data.get("prompt_tokens", 0))
    tokens_out = req_data.get("tokens_out", req_data.get("completion_tokens", 0))
    saved_tokens = req_data.get("saved_tokens", 0)
    saved_dollars = req_data.get("saved_dollars", req_data.get("cost_saved_usd", 0.0))

    # Compression
    compression_ratio = req_data.get("avg_compression_ratio", req_data.get("compression_ratio", 0.0))
    if isinstance(compression_ratio, float) and compression_ratio > 1.0:
        compression_pct = f"{(1.0 - 1.0/compression_ratio)*100:.0f}%"
    elif isinstance(compression_ratio, (int, float)) and 0 < compression_ratio < 1:
        compression_pct = f"{compression_ratio*100:.0f}%"
    else:
        compression_pct = "n/a"

    # Auth profiles
    profiles = _load_auth_profiles()

    # Recent errors from degradation endpoint
    recent_errors: List[str] = []
    if degradation:
        for ev in degradation.get("recent_events", [])[:3]:
            recent_errors.append(ev.get("detail", str(ev))[:80])

    error_rate_pct = (errors / max(requests, 1)) * 100

    return {
        "proxy_running": proxy_running,
        "proxy_status": health.get("status", "unknown") if health else "stopped",
        "proxy_port": PROXY_PORT,
        "start_time": _proxy_start_time(),
        "requests": requests,
        "errors": errors,
        "error_rate_pct": error_rate_pct,
        "avg_latency_ms": avg_latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "saved_tokens": saved_tokens,
        "saved_dollars": saved_dollars,
        "compression_pct": compression_pct,
        "compression_mode": req_data.get("compression_mode", "hybrid"),
        "auth_profiles": profiles,
        "recent_errors": recent_errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def collect_fleet_data() -> List[Dict[str, Any]]:
    """Gather data from all fleet agents via SSH."""
    import subprocess
    import concurrent.futures

    if not FLEET_CONFIG_FILE.exists():
        return [collect_local_data()]

    try:
        import yaml
        with open(FLEET_CONFIG_FILE) as f:
            fleet_cfg = yaml.safe_load(f)
    except Exception:
        return [collect_local_data()]

    agents = fleet_cfg.get("agents", [])
    if not agents:
        return [collect_local_data()]

    def _fetch_remote(agent: dict) -> Dict[str, Any]:
        name = agent.get("name", "?")
        host = agent.get("host", "")
        user = agent.get("user", "")
        target = f"{user}@{host}" if user else host
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 target, "tokenpak dashboard --json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                data["agent_name"] = name
                return data
        except Exception as exc:
            pass
        return {
            "agent_name": name,
            "proxy_running": False,
            "proxy_status": "unreachable",
            "proxy_port": PROXY_PORT,
            "requests": 0, "errors": 0, "error_rate_pct": 0,
            "avg_latency_ms": 0, "tokens_in": 0, "tokens_out": 0,
            "saved_dollars": 0.0, "compression_pct": "n/a",
            "auth_profiles": {}, "recent_errors": ["SSH unreachable"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
        return list(ex.map(_fetch_remote, agents))


# ---------------------------------------------------------------------------
# Rendering (rich-based)
# ---------------------------------------------------------------------------

def _render_dashboard(data: Dict[str, Any]) -> None:
    """Print a single dashboard frame using rich."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.columns import Columns
        from rich import box
        from rich.text import Text
    except ImportError:
        _render_plain(data)
        return

    console = Console()
    now_str = datetime.now().strftime("%H:%M:%S")

    # Header
    console.print(f"[bold cyan]TokenPak Dashboard[/bold cyan]  [dim]{now_str}[/dim]")
    console.print("─" * 50)

    # Proxy status line
    proxy_icon = "[green]✓ running[/green]" if data["proxy_running"] else "[red]✗ stopped[/red]"
    uptime = _uptime_str(data.get("start_time"))
    console.print(
        f"Proxy: {proxy_icon} on :{data['proxy_port']} "
        f"[dim](uptime: {uptime})[/dim]  "
        f"Compression: [yellow]{data.get('compression_mode','hybrid')}[/yellow] | "
        f"[green]{data.get('compression_pct','n/a')}[/green] avg savings"
    )
    console.print()

    # Stats table
    stats_table = Table(box=box.ROUNDED, expand=True, title="Last Hour")
    stats_table.add_column("Metric", style="bold")
    stats_table.add_column("Value", justify="right")

    err_rate = data.get("error_rate_pct", 0)
    err_color = "red" if err_rate > 5 else "yellow" if err_rate > 1 else "green"
    stats_table.add_row("Requests", f"{data.get('requests', 0):,}")
    stats_table.add_row("Errors", f"[{err_color}]{data.get('errors', 0):,} ({err_rate:.1f}%)[/{err_color}]")
    stats_table.add_row("Avg Latency", f"{data.get('avg_latency_ms', 0):.0f}ms")
    stats_table.add_row("Tokens in", f"{data.get('tokens_in', 0):,}")
    stats_table.add_row("Tokens out", f"{data.get('tokens_out', 0):,}")
    stats_table.add_row("Saved tokens", f"[green]{data.get('saved_tokens', 0):,}[/green]")
    stats_table.add_row("Saved $", f"[green]${data.get('saved_dollars', 0.0):.2f}[/green]")
    console.print(stats_table)
    console.print()

    # Auth profiles
    profiles = data.get("auth_profiles", {})
    if profiles:
        auth_table = Table(box=box.ROUNDED, expand=True, title="Auth Profiles")
        auth_table.add_column("Profile")
        auth_table.add_column("Status", justify="right")
        for name, profile in profiles.items():
            icon, desc = _profile_status(profile)
            color = "green" if icon == "✓" else "yellow" if icon == "⚠" else "red"
            auth_table.add_row(name, f"[{color}]{icon} {desc}[/{color}]")
        console.print(auth_table)
        console.print()

    # Recent errors
    recent_errors = data.get("recent_errors", [])
    if recent_errors:
        console.print("[yellow]Recent errors:[/yellow]")
        for err in recent_errors:
            console.print(f"  [red]•[/red] {err}")
        console.print()


def _render_fleet_dashboard(fleet_data: List[Dict[str, Any]]) -> None:
    """Print fleet-wide summary."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
    except ImportError:
        for d in fleet_data:
            _render_plain(d)
        return

    console = Console()
    now_str = datetime.now().strftime("%H:%M:%S")
    console.print(f"[bold cyan]TokenPak Fleet Dashboard[/bold cyan]  [dim]{now_str}[/dim]")
    console.print("─" * 60)

    t = Table(box=box.ROUNDED, expand=True)
    t.add_column("Agent")
    t.add_column("Status")
    t.add_column("Requests", justify="right")
    t.add_column("Errors", justify="right")
    t.add_column("Latency", justify="right")
    t.add_column("Saved $", justify="right")

    for d in fleet_data:
        name = d.get("agent_name", "?")
        running = d.get("proxy_running", False)
        status_str = "[green]✓ running[/green]" if running else "[red]✗ down[/red]"
        err_rate = d.get("error_rate_pct", 0)
        err_color = "red" if err_rate > 5 else "yellow" if err_rate > 1 else "green"
        t.add_row(
            name,
            status_str,
            f"{d.get('requests', 0):,}",
            f"[{err_color}]{d.get('errors', 0):,}[/{err_color}]",
            f"{d.get('avg_latency_ms', 0):.0f}ms",
            f"[green]${d.get('saved_dollars', 0.0):.2f}[/green]",
        )

    console.print(t)


def _render_plain(data: Dict[str, Any]) -> None:
    """Fallback plain-text render (no rich)."""
    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"\nTokenPak Dashboard  [{now_str}]")
    print("─" * 50)
    proxy_status = "running" if data.get("proxy_running") else "stopped"
    print(f"Proxy: {proxy_status} on :{data.get('proxy_port', PROXY_PORT)}")
    print(f"Requests: {data.get('requests', 0)}  Errors: {data.get('errors', 0)}")
    print(f"Tokens in: {data.get('tokens_in', 0)}  Tokens out: {data.get('tokens_out', 0)}")
    print(f"Saved: ${data.get('saved_dollars', 0.0):.2f}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_dashboard(fleet: bool = False, json_export: bool = False) -> None:
    """Run the dashboard (interactive TUI or one-shot JSON)."""
    if json_export:
        data = collect_local_data()
        print(json.dumps(data, indent=2))
        return

    if fleet:
        # Fleet mode — non-interactive summary, refreshes in loop
        try:
            while True:
                _clear_screen()
                fleet_data = collect_fleet_data()
                _render_fleet_dashboard(fleet_data)
                print(f"\n[Refreshing every {REFRESH_INTERVAL}s — Ctrl-C to quit]")
                time.sleep(REFRESH_INTERVAL)
        except KeyboardInterrupt:
            print("\nDashboard closed.")
        return

    # Local TUI mode
    try:
        while True:
            _clear_screen()
            data = collect_local_data()
            _render_dashboard(data)
            print(f"[Refreshing every {REFRESH_INTERVAL}s — press Ctrl-C to quit]")
            time.sleep(REFRESH_INTERVAL)
    except KeyboardInterrupt:
        print("\nDashboard closed.")


def _clear_screen() -> None:
    """Clear terminal screen."""
    print("\033[2J\033[H", end="", flush=True)


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------

try:
    import click

    @click.command("dashboard")
    @click.option("--fleet", is_flag=True, help="Show fleet-wide summary from all registered agents")
    @click.option("--json", "json_export", is_flag=True, help="Export dashboard data as JSON (non-interactive)")
    def dashboard_cmd(fleet: bool, json_export: bool) -> None:
        """Real-time TokenPak health dashboard.

        Shows proxy status, request stats, compression savings, and auth profiles.
        Refreshes every 5 seconds. Press Ctrl-C to quit.

        Examples:

        \\b
          tokenpak dashboard            # local TUI
          tokenpak dashboard --fleet    # fleet-wide summary
          tokenpak dashboard --json     # one-shot JSON export
        """
        run_dashboard(fleet=fleet, json_export=json_export)

except ImportError:

    def dashboard_cmd(*args, **kwargs):  # type: ignore
        print("click not installed; dashboard command unavailable")
