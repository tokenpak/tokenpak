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
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tokenpak import _paths
from tokenpak.platform.capabilities import _detect_dashboard_capabilities

try:
    PROXY_PORT = int(os.environ.get("TOKENPAK_PORT", "8766"))
except (TypeError, ValueError):
    PROXY_PORT = 8766

SCHEMA_VERSION = "dashboard.v2.0"
REFRESH_INTERVAL = 5  # seconds

__all__ = [
    "PROXY_PORT",
    "REFRESH_INTERVAL",
    "collect_fleet_data",
    "collect_local_data",
    "run_dashboard",
]


def _proxy_port() -> int:
    try:
        return int(os.environ.get("TOKENPAK_PORT", str(PROXY_PORT)))
    except (TypeError, ValueError):
        return PROXY_PORT


def _auth_profiles_file():
    return _paths.under("auth-profiles.json")


def _fleet_config_file():
    return _paths.under("fleet.yaml")


def _proxy_pid_file():
    return _paths.under("proxy.pid")


def _dispatch_runs_db():
    return _paths.under("dispatch", "runs.db")


def _companion_journal_db():
    return _paths.under("companion", "journal.db")


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------


def _http_get(path: str, port: int | None = None, timeout: float = 3.0) -> Optional[Dict]:
    """Fetch JSON from proxy management endpoint. Returns None on failure."""
    port = _proxy_port() if port is None else port
    try:
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _proxy_start_time() -> Optional[float]:
    """Best-effort proxy start time from the canonical pid-file mtime."""
    pid_file = _proxy_pid_file()
    if pid_file.exists():
        try:
            return pid_file.stat().st_mtime
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
    auth_profiles_file = _auth_profiles_file()
    if not auth_profiles_file.exists():
        return {}
    try:
        data = json.loads(auth_profiles_file.read_text())
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


def _source(kind: str, ref: str, *, available: bool, detail: str | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": kind,
        "ref": ref,
        "state": "available" if available else "unavailable",
    }
    if detail:
        payload["detail"] = detail
    return payload


def _measure(
    value: Any,
    *,
    state: str,
    source: str,
    unit: str | None = None,
    detail: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"state": state, "value": value, "source": source}
    if unit:
        payload["unit"] = unit
    if detail:
        payload["detail"] = detail
    return payload


def _field_measure(
    data: Dict[str, Any] | None,
    keys: tuple[str, ...],
    *,
    source: str,
    unit: str | None = None,
    missing_state: str = "not_measured",
) -> Dict[str, Any]:
    if not data:
        return _measure(None, state=missing_state, source=source, unit=unit)
    for key in keys:
        if key in data and data[key] is not None:
            return _measure(data[key], state="measured", source=source, unit=unit)
    return _measure(None, state=missing_state, source=source, unit=unit)


def _compression_percent(data: Dict[str, Any] | None) -> Dict[str, Any]:
    if not data:
        return _measure(None, state="not_measured", source="proxy_stats", unit="percent")
    ratio = data.get("avg_compression_ratio", data.get("compression_ratio"))
    if not isinstance(ratio, (int, float)) or ratio <= 0:
        return _measure(None, state="not_measured", source="proxy_stats", unit="percent")
    if ratio > 1.0:
        value = (1.0 - 1.0 / ratio) * 100
    else:
        value = ratio * 100
    return _measure(round(value, 1), state="measured", source="proxy_stats", unit="percent")


def collect_dashboard_snapshot() -> Dict[str, Any]:
    """Return the stable read-only ``tokenpak dashboard --json`` contract."""
    port = _proxy_port()
    health = _http_get("/health", port=port)
    stats = _http_get("/stats", port=port)
    stats_session = _http_get("/stats/session", port=port)
    degradation = _http_get("/degradation", port=port)
    req_data = stats or stats_session

    auth_profiles_path = _auth_profiles_file()
    fleet_config_path = _fleet_config_file()
    dispatch_runs_db = _dispatch_runs_db()
    companion_journal_db = _companion_journal_db()
    monitor_db = _paths.monitor_db(mode="read")

    sources: Dict[str, Any] = {
        "proxy_health": _source("http", "/health", available=health is not None),
        "proxy_stats": _source(
            "http",
            "/stats" if stats is not None else "/stats/session",
            available=req_data is not None,
        ),
        "proxy_degradation": _source("http", "/degradation", available=degradation is not None),
        "auth_profiles": _source(
            "file",
            "tokenpak-home/auth-profiles.json",
            available=auth_profiles_path.exists(),
        ),
        "monitor_db": _source(
            "sqlite",
            "tokenpak._paths.monitor_db(mode='read')",
            available=monitor_db is not None,
        ),
        "dispatch_runs": _source(
            "sqlite",
            "tokenpak-home/dispatch/runs.db",
            available=dispatch_runs_db.exists(),
        ),
        "companion_journal": _source(
            "sqlite",
            "tokenpak-home/companion/journal.db",
            available=companion_journal_db.exists(),
        ),
        "fleet_config": _source(
            "file",
            "tokenpak-home/fleet.yaml",
            available=fleet_config_path.exists(),
        ),
    }

    warnings: list[str] = []
    if health is None:
        warnings.append("proxy health unavailable; proxy state is unknown")
    if req_data is None:
        warnings.append("proxy stats unavailable; spend and request values are not measured")
    if monitor_db is None:
        warnings.append("monitor.db unavailable; historical receipt values are not measured")

    proxy_status = health.get("status") if isinstance(health, dict) else None
    if proxy_status in {"ok", "degraded"}:
        proxy_state = "running"
    elif proxy_status:
        proxy_state = str(proxy_status)
    else:
        proxy_state = "unknown"

    recent_errors: List[str] = []
    if isinstance(degradation, dict):
        for ev in degradation.get("recent_events", [])[:3]:
            recent_errors.append(ev.get("detail", str(ev))[:80])

    profiles = _load_auth_profiles()
    capabilities = _detect_dashboard_capabilities(
        monitor_db_available=monitor_db is not None,
        dispatch_state_available=dispatch_runs_db.exists(),
        companion_state_available=companion_journal_db.exists(),
        fleet_config_exists=fleet_config_path.exists(),
    )

    proxy_start_time = _proxy_start_time()

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": capabilities,
        "sources": sources,
        "summary": {
            "proxy": {
                "state": proxy_state,
                "status": proxy_status or "unknown",
                "source": "proxy_health",
                "port": port,
            },
            "requests": _field_measure(
                req_data, ("requests", "total_requests"), source="proxy_stats", unit="count"
            ),
            "errors": _field_measure(
                req_data, ("errors", "total_errors"), source="proxy_stats", unit="count"
            ),
            "avg_latency_ms": _field_measure(
                req_data, ("avg_latency_ms", "latency_avg_ms"), source="proxy_stats", unit="ms"
            ),
            "tokens_in": _field_measure(
                req_data, ("tokens_in", "prompt_tokens"), source="proxy_stats", unit="tokens"
            ),
            "tokens_out": _field_measure(
                req_data,
                ("tokens_out", "completion_tokens"),
                source="proxy_stats",
                unit="tokens",
            ),
            "auth_profiles": _measure(
                len(profiles) if auth_profiles_path.exists() else None,
                state="measured" if auth_profiles_path.exists() else "not_configured",
                source="auth_profiles",
                unit="count",
            ),
            "recent_errors": _measure(
                recent_errors,
                state="measured" if degradation is not None else "unknown",
                source="proxy_degradation",
            ),
            "proxy_start_time": _measure(
                datetime.fromtimestamp(proxy_start_time, timezone.utc).isoformat()
                if proxy_start_time
                else None,
                state="inferred" if proxy_start_time else "unknown",
                source="tokenpak-home/proxy.pid.mtime",
            ),
        },
        "dispatch": {
            "state": "available" if dispatch_runs_db.exists() else "not_configured",
            "source": "dispatch_runs",
            "read_only": True,
        },
        "companion": {
            "state": "available" if companion_journal_db.exists() else "not_configured",
            "source": "companion_journal",
            "read_only": True,
        },
        "spend": {
            "cost_usd": _field_measure(
                req_data,
                ("cost_usd", "estimated_cost", "total_cost_usd"),
                source="proxy_stats",
                unit="usd",
            ),
            "saved_tokens": _field_measure(
                req_data, ("saved_tokens",), source="proxy_stats", unit="tokens"
            ),
            "saved_usd": _field_measure(
                req_data,
                ("saved_dollars", "cost_saved_usd", "saved_usd"),
                source="proxy_stats",
                unit="usd",
            ),
            "compression_percent": _compression_percent(req_data),
            "compression_mode": _field_measure(
                req_data, ("compression_mode",), source="proxy_stats", missing_state="unknown"
            ),
        },
        "debug": {
            "tokenpak_home": str(_paths.home()),
            "legacy_home_active": _paths.is_legacy(),
            "proxy_pid_file": "tokenpak-home/proxy.pid",
            "fleet_default_enabled": False,
        },
        "warnings": warnings,
    }


def _legacy_value(measure: Dict[str, Any], fallback: Any = "not measured") -> Any:
    return measure.get("value") if measure.get("state") in {"measured", "inferred"} else fallback


def _legacy_view_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    summary = snapshot["summary"]
    spend = snapshot["spend"]
    requests = _legacy_value(summary["requests"], None)
    errors = _legacy_value(summary["errors"], None)
    if isinstance(requests, (int, float)) and isinstance(errors, (int, float)) and requests > 0:
        error_rate_pct: Any = (errors / requests) * 100
    elif isinstance(requests, (int, float)) and isinstance(errors, (int, float)):
        error_rate_pct = 0.0
    else:
        error_rate_pct = None
    compression_pct = _legacy_value(spend["compression_percent"], None)
    return {
        "proxy_running": summary["proxy"]["state"] == "running",
        "proxy_status": summary["proxy"]["status"],
        "proxy_port": summary["proxy"]["port"],
        "start_time": None,
        "requests": requests,
        "errors": errors,
        "error_rate_pct": error_rate_pct,
        "avg_latency_ms": _legacy_value(summary["avg_latency_ms"], None),
        "tokens_in": _legacy_value(summary["tokens_in"], None),
        "tokens_out": _legacy_value(summary["tokens_out"], None),
        "saved_tokens": _legacy_value(spend["saved_tokens"], None),
        "saved_dollars": _legacy_value(spend["saved_usd"], None),
        "compression_pct": f"{compression_pct:.0f}%" if isinstance(compression_pct, (int, float)) else "not measured",
        "compression_mode": _legacy_value(spend["compression_mode"], "unknown"),
        "auth_profiles": _load_auth_profiles(),
        "recent_errors": _legacy_value(summary["recent_errors"], []),
        "timestamp": snapshot["generated_at"],
    }


def collect_local_data() -> Dict[str, Any]:
    """Gather all data for the local dashboard view."""
    return _legacy_view_from_snapshot(collect_dashboard_snapshot())


def collect_fleet_data() -> List[Dict[str, Any]]:
    """Gather data from configured hosts via SSH."""
    import concurrent.futures
    import subprocess

    fleet_config_file = _fleet_config_file()
    if not fleet_config_file.exists():
        return [collect_local_data()]

    try:
        import yaml

        with open(fleet_config_file) as f:
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
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=5",
                    "-o",
                    "BatchMode=yes",
                    target,
                    "tokenpak dashboard --json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if data.get("schema_version") == SCHEMA_VERSION:
                    data = _legacy_view_from_snapshot(data)
                data["agent_name"] = name
                return data
        except Exception:
            pass
        return {
            "agent_name": name,
            "proxy_running": False,
            "proxy_status": "unreachable",
            "proxy_port": _proxy_port(),
            "requests": None,
            "errors": None,
            "error_rate_pct": None,
            "avg_latency_ms": None,
            "tokens_in": None,
            "tokens_out": None,
            "saved_dollars": None,
            "compression_pct": "not measured",
            "auth_profiles": {},
            "recent_errors": ["SSH unreachable"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
        return list(ex.map(_fetch_remote, agents))


# ---------------------------------------------------------------------------
# Rendering (rich-based)
# ---------------------------------------------------------------------------


def _fmt_count(value: Any) -> str:
    return f"{int(value):,}" if isinstance(value, (int, float)) else "not measured"


def _fmt_ms(value: Any) -> str:
    return f"{float(value):.0f}ms" if isinstance(value, (int, float)) else "not measured"


def _fmt_cost(value: Any) -> str:
    return f"${float(value):.2f}" if isinstance(value, (int, float)) else "not measured"


def _fmt_error_rate(value: Any) -> str:
    return f"{float(value):.1f}%" if isinstance(value, (int, float)) else "not measured"


def _render_dashboard(data: Dict[str, Any]) -> None:
    """Print a single dashboard frame using rich."""
    try:
        from rich import box
        from rich.columns import Columns  # noqa: F401  # availability check
        from rich.console import Console
        from rich.panel import Panel  # noqa: F401  # availability check
        from rich.table import Table
        from rich.text import Text  # noqa: F401  # availability check
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
    if isinstance(err_rate, (int, float)):
        err_color = "red" if err_rate > 5 else "yellow" if err_rate > 1 else "green"
    else:
        err_color = "yellow"
    stats_table.add_row("Requests", _fmt_count(data.get("requests")))
    stats_table.add_row(
        "Errors",
        f"[{err_color}]{_fmt_count(data.get('errors'))} "
        f"({_fmt_error_rate(err_rate)})[/{err_color}]",
    )
    stats_table.add_row("Avg Latency", _fmt_ms(data.get("avg_latency_ms")))
    stats_table.add_row("Tokens in", _fmt_count(data.get("tokens_in")))
    stats_table.add_row("Tokens out", _fmt_count(data.get("tokens_out")))
    stats_table.add_row("Saved tokens", f"[green]{_fmt_count(data.get('saved_tokens'))}[/green]")
    stats_table.add_row("Saved $", f"[green]{_fmt_cost(data.get('saved_dollars'))}[/green]")
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
        from rich import box
        from rich.console import Console
        from rich.table import Table
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
        err_rate = d.get("error_rate_pct")
        if isinstance(err_rate, (int, float)):
            err_color = "red" if err_rate > 5 else "yellow" if err_rate > 1 else "green"
        else:
            err_color = "yellow"
        t.add_row(
            name,
            status_str,
            _fmt_count(d.get("requests")),
            f"[{err_color}]{_fmt_count(d.get('errors'))}[/{err_color}]",
            _fmt_ms(d.get("avg_latency_ms")),
            f"[green]{_fmt_cost(d.get('saved_dollars'))}[/green]",
        )

    console.print(t)


def _render_plain(data: Dict[str, Any]) -> None:
    """Fallback plain-text render (no rich)."""
    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"\nTokenPak Dashboard  [{now_str}]")
    print("─" * 50)
    proxy_status = "running" if data.get("proxy_running") else "stopped"
    print(f"Proxy: {proxy_status} on :{data.get('proxy_port', _proxy_port())}")
    print(f"Requests: {_fmt_count(data.get('requests'))}  Errors: {_fmt_count(data.get('errors'))}")
    print(
        f"Tokens in: {_fmt_count(data.get('tokens_in'))}  "
        f"Tokens out: {_fmt_count(data.get('tokens_out'))}"
    )
    print(f"Saved: {_fmt_cost(data.get('saved_dollars'))}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_dashboard(fleet: bool = False, json_export: bool = False) -> None:
    """Run the dashboard (interactive TUI or one-shot JSON)."""
    if json_export:
        data = collect_dashboard_snapshot()
        print(json.dumps(data, indent=2, sort_keys=True))
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
    @click.option(
        "--fleet", is_flag=True, help="Show fleet-wide summary from all registered agents"
    )
    @click.option(
        "--json",
        "json_export",
        is_flag=True,
        help="Export dashboard data as JSON (non-interactive)",
    )
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
