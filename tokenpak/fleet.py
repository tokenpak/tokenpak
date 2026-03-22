# SPDX-License-Identifier: MIT
"""Fleet management: querying multiple TokenPak proxy instances."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class FleetMachine:
    """Single machine in the fleet."""
    name: str
    host: str
    port: int
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FleetStats:
    """Stats from a single machine."""
    name: str
    requests: int = 0
    saved: int = 0
    cache_pct: float = 0.0
    compression: float = 0.0
    health: str = "❌"  # ✅, ⚠️, or ❌
    error: Optional[str] = None
    cost: float = 0.0
    cost_saved: float = 0.0
    cache_read_tokens: int = 0


@dataclass
class FleetAgentRow:
    """Per-agent breakdown row."""
    machine: str
    agent: str
    model: str
    requests: int = 0
    tokens: int = 0
    cost: float = 0.0
    saved: float = 0.0


# ── Fleet configuration ───────────────────────────────────────────────────────

def _get_fleet_config_path() -> Path:
    """Get the fleet.yaml config path."""
    return Path.home() / ".tokenpak" / "fleet.yaml"


def load_fleet_config() -> List[FleetMachine]:
    """Load fleet.yaml and return list of machines."""
    config_path = _get_fleet_config_path()
    
    if not config_path.exists():
        return []
    
    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
        
        # Support both old "agents" key and new "fleet" key
        machines_data = data.get("fleet") or data.get("agents") or []
        machines = []
        
        for item in machines_data:
            machine = FleetMachine(
                name=item.get("name", "unknown"),
                host=item.get("host", "localhost"),
                port=item.get("port", 8766),
            )
            machines.append(machine)
        
        return machines
    except Exception as e:
        print(f"Error loading fleet config: {e}", file=sys.stderr)
        return []


def save_fleet_config(machines: List[FleetMachine]):
    """Save machines to fleet.yaml."""
    config_path = _get_fleet_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    data = {
        "fleet": [m.to_dict() for m in machines]
    }
    
    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ── Health & stats queries ────────────────────────────────────────────────────

def _query_machine_aggregate(machine: FleetMachine, timeout: float = 3.0, since: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    """Query per-agent breakdown from /stats/aggregate/local."""
    try:
        url = f"http://{machine.host}:{machine.port}/stats/aggregate/local"
        if since:
            url += f"?since={since}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read())
        return payload.get("rows", []), None
    except urllib.error.URLError as e:
        return [], str(e)
    except Exception as e:
        return [], str(e)


def query_fleet_agent_rows(machines: List[FleetMachine], since: Optional[str] = None) -> tuple[list[FleetAgentRow], list[str]]:
    """Query all machines for per-agent rows."""
    rows: list[FleetAgentRow] = []
    errors: list[str] = []
    for machine in machines:
        data, err = _query_machine_aggregate(machine, since=since)
        if err:
            errors.append(f"{machine.name}: {err}")
            continue
        for row in data:
            rows.append(FleetAgentRow(
                machine=row.get("machine", machine.name),
                agent=row.get("agent", "unknown"),
                model=row.get("model", "unknown"),
                requests=int(row.get("requests", 0) or 0),
                tokens=int(row.get("tokens", 0) or 0),
                cost=float(row.get("cost", 0.0) or 0.0),
                saved=float(row.get("saved", 0.0) or 0.0),
            ))
    return rows, errors


def _query_machine(machine: FleetMachine, timeout: float = 3.0) -> FleetStats:
    """Query a single machine for health and stats."""
    stats = FleetStats(name=machine.name)
    
    try:
        # Query /health endpoint
        health_url = f"http://{machine.host}:{machine.port}/health"
        with urllib.request.urlopen(health_url, timeout=timeout) as resp:
            health_data = json.loads(resp.read())
        
        # Query /stats endpoint
        stats_url = f"http://{machine.host}:{machine.port}/stats"
        with urllib.request.urlopen(stats_url, timeout=timeout) as resp:
            stats_data = json.loads(resp.read())
        
        # Extract stats
        session_stats = stats_data.get("session", {})
        stats.requests = session_stats.get("requests", 0)
        stats.saved = session_stats.get("saved_tokens", 0)
        stats.cost = session_stats.get("cost", 0.0)
        stats.cost_saved = session_stats.get("cost_saved", 0.0)
        stats.cache_read_tokens = session_stats.get("cache_read_tokens", 0)
        
        # Calculate cache percentage
        inp = session_stats.get("input_tokens", 0)
        sent = session_stats.get("sent_input_tokens", 0)
        stats.cache_pct = ((inp - sent) / inp * 100) if inp > 0 else 0.0
        
        # Calculate compression
        total_out = session_stats.get("output_tokens", 0)
        if total_out > 0:
            sent_out = session_stats.get("sent_output_tokens", 0)
            stats.compression = ((total_out - sent_out) / total_out * 100)
        
        # Determine health status
        health_status = health_data.get("status", "unknown")
        if health_status in ("ok", "healthy"):
            stats.health = "✅"
        elif health_status in ("degraded", "warning"):
            stats.health = "⚠️"
        else:
            stats.health = "❌"
    
    except urllib.error.URLError as e:
        stats.health = "❌"
        stats.error = str(e)
    except Exception as e:
        stats.health = "❌"
        stats.error = str(e)
    
    return stats


def query_fleet(machines: List[FleetMachine]) -> List[FleetStats]:
    """Query all machines in the fleet."""
    results = []
    for machine in machines:
        stats = _query_machine(machine)
        results.append(stats)
    return results


# ── Rendering ────────────────────────────────────────────────────────────────

def _fmt_cost(amount: float) -> str:
    """Format a dollar amount compactly."""
    if amount >= 1.0:
        return f"${amount:.2f}"
    elif amount >= 0.01:
        return f"${amount:.2f}"
    else:
        return f"${amount:.4f}"


def _fmt_tokens(n: int) -> str:
    """Format token count compactly (e.g., 1.2M, 342K)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.0f}K"
    else:
        return str(n)


def _calc_savings(s: "FleetStats") -> tuple:
    """Return (compression_saved_$, cache_saved_$, total_saved_$).
    
    Compression: tokens removed before sending (input - sent) at full input rate.
    Caching: cache_read_tokens at 90% discount (pay $0.30 instead of $3.00/MTok).
    Rate assumes Sonnet-class pricing ($3/MTok input). Adjust via TOKENPAK_INPUT_RATE.
    """
    import os
    rate = float(os.environ.get("TOKENPAK_INPUT_RATE", "3.0"))  # $/MTok

    comp_saved = (s.saved / 1_000_000) * rate
    cache_saved = (s.cache_read_tokens / 1_000_000) * (rate * 0.9)
    return comp_saved, cache_saved, comp_saved + cache_saved


def render_fleet_table(stats_list: List[FleetStats], compact: bool = False) -> str:
    """Render fleet stats — savings-focused, minimal format."""
    if not stats_list:
        return "No machines configured in fleet."

    lines = []
    total_cost = 0.0
    total_comp = 0.0
    total_cache = 0.0
    total_requests = 0

    for s in stats_list:
        comp, cache, total = _calc_savings(s)
        total_cost += s.cost
        total_comp += comp
        total_cache += cache
        total_requests += s.requests

        line = f"{s.health} {s.name}: {s.requests} reqs | spent {_fmt_cost(s.cost)} | 💰 saved {_fmt_cost(total)} (compression {_fmt_cost(comp)}, cache {_fmt_cost(cache)})"
        lines.append(line)

    grand_saved = total_comp + total_cache
    lines.append("")
    lines.append(f"Fleet: {total_requests} reqs | spent {_fmt_cost(total_cost)} | 💰 saved {_fmt_cost(grand_saved)} (compression {_fmt_cost(total_comp)}, cache {_fmt_cost(total_cache)})")

    return "\n".join(lines)


def render_fleet_agent_table(rows: List[FleetAgentRow]) -> str:
    """Render per-agent breakdown for the fleet."""
    if not rows:
        return "No fleet agent data available."

    header = "Machine  Agent   Model             Requests  Tokens   Spent    Saved"
    lines = [header, "─" * len(header)]

    total_requests = 0
    total_tokens = 0
    total_cost = 0.0
    total_saved = 0.0

    rows_sorted = sorted(rows, key=lambda r: (-r.cost, r.machine, r.agent, r.model))
    for r in rows_sorted:
        lines.append(
            f"{r.machine:<8} {r.agent:<7} {r.model:<17} {r.requests:>8}  {_fmt_tokens(r.tokens):>6}  {_fmt_cost(r.cost):>7}  {_fmt_cost(r.saved):>7}"
        )
        total_requests += r.requests
        total_tokens += r.tokens
        total_cost += r.cost
        total_saved += r.saved

    lines.append("─" * len(header))
    lines.append(
        f"TOTAL{'':<3}{'':<7}{'':<17} {total_requests:>8}  {_fmt_tokens(total_tokens):>6}  {_fmt_cost(total_cost):>7}  {_fmt_cost(total_saved):>7}"
    )
    return "\n".join(lines)


def render_fleet_json(stats_list: List[FleetStats]) -> str:
    """Render fleet stats as JSON."""
    data = {
        "machines": [asdict(s) for s in stats_list],
        "timestamp": time.time(),
    }
    
    # Add totals
    totals = {
        "requests": sum(s.requests for s in stats_list),
        "saved": sum(s.saved for s in stats_list),
    }
    data["totals"] = totals
    
    return json.dumps(data, indent=2)


# ── Interactive setup ────────────────────────────────────────────────────────

def interactive_add_machine(machines: List[FleetMachine]) -> Optional[FleetMachine]:
    """Prompt user to add a new machine to the fleet."""
    print("\n📋 Add machine to fleet")
    
    name = input("Machine name (e.g., 'sue', 'trix'): ").strip()
    if not name:
        print("Cancelled (no name provided)")
        return None
    
    # Check for duplicates
    if any(m.name == name for m in machines):
        print(f"⚠️  Machine '{name}' already exists in fleet")
        return None
    
    host = input(f"Host/IP (default: localhost): ").strip() or "localhost"
    
    port_str = input(f"Port (default: 8766): ").strip() or "8766"
    try:
        port = int(port_str)
    except ValueError:
        print(f"Invalid port: {port_str}")
        return None
    
    # Create machine
    machine = FleetMachine(name=name, host=host, port=port)
    
    # Test connection
    print(f"\n⏳ Testing connection to {host}:{port}...")
    stats = _query_machine(machine, timeout=3.0)
    
    if stats.health == "✅":
        print(f"✅ {name} is healthy!")
        return machine
    else:
        print(f"⚠️  {name} is {stats.health} (may be offline or unreachable)")
        confirm = input("Add anyway? (y/n): ").strip().lower()
        if confirm == "y":
            return machine
        else:
            print("Cancelled")
            return None
