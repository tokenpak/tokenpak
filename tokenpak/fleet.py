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


def render_fleet_table(stats_list: List[FleetStats], compact: bool = False) -> str:
    """Render fleet stats — savings-focused, minimal format."""
    if not stats_list:
        return "No machines configured in fleet."

    lines = []
    total_cost = 0.0
    total_saved_cost = 0.0
    total_cache_read = 0
    total_requests = 0

    for s in stats_list:
        # Estimate savings from cache reads (90% discount on cached tokens)
        cache_savings = s.cache_read_tokens * 0.9  # tokens that cost 10% instead of 100%
        total_cost += s.cost
        total_saved_cost += s.cost_saved
        total_cache_read += s.cache_read_tokens
        total_requests += s.requests

        status = s.health
        line = f"{status} {s.name}: {s.requests} reqs, {_fmt_cost(s.cost)} spent, cache {s.cache_pct:.0f}%, {_fmt_tokens(s.cache_read_tokens)} cached reads"
        lines.append(line)

    lines.append("")
    lines.append(f"Fleet: {total_requests} reqs, {_fmt_cost(total_cost)} total, {_fmt_tokens(total_cache_read)} cache reads")

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
