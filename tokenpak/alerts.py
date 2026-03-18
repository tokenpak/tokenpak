# SPDX-License-Identifier: MIT
"""TokenPak Alert Rules and Health Monitoring

Alert system for TokenPak proxy health with rule evaluation,
cooldown management, and persistent state tracking.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any

try:
    import yaml as _yaml

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _yaml.safe_load(f) or {}

except ImportError:

    def _load_yaml(path: str) -> dict:
        import json
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}


@dataclass
class AlertRule:
    """Single alert rule definition."""

    name: str
    condition: str  # Simple threshold condition (e.g., "cache_hit_rate < 0.80")
    message: str  # Message template (can use {value} placeholder)
    cooldown_minutes: int = 30


@dataclass
class AlertRuleState:
    """Tracks state for a single alert rule (cooldown enforcement)."""

    name: str
    last_fired: Optional[float] = None
    last_value: Optional[float] = None
    fired_count: int = 0

    def should_fire(self, cooldown_minutes: int) -> bool:
        """Check if enough time has passed since last fire."""
        if self.last_fired is None:
            return True
        elapsed_minutes = (time.time() - self.last_fired) / 60
        return elapsed_minutes >= cooldown_minutes

    def update_fired(self, value: float = 0.0):
        """Record that this alert fired."""
        self.last_fired = time.time()
        self.last_value = value
        self.fired_count += 1

    def to_dict(self) -> dict:
        return asdict(self)


def _get_config_path() -> Path:
    """Return path to config file."""
    return Path.home() / ".tokenpak" / "config.yaml"


def _get_state_path() -> Path:
    """Return path to alert state file."""
    return Path.home() / ".tokenpak" / "alert_state.json"


def _get_default_rules() -> list[AlertRule]:
    """Get default alert rules."""
    return [
        AlertRule(
            name="cache_drop",
            condition="cache_hit_rate < 0.80",
            message="⚠️ Cache hit rate dropped to {value:.0f}%",
            cooldown_minutes=30,
        ),
        AlertRule(
            name="error_spike",
            condition="error_rate > 0.05",
            message="🔴 Error rate at {value:.1f}% — check proxy health",
            cooldown_minutes=15,
        ),
        AlertRule(
            name="proxy_down",
            condition="health != 'ok'",
            message="❌ TokenPak proxy is down!",
            cooldown_minutes=5,
        ),
    ]


def load_config() -> dict:
    """Load alert configuration from ~/.tokenpak/config.yaml.

    Returns config with 'alerts' key containing rules and settings.
    Creates default config if file doesn't exist.
    """
    config_path = _get_config_path()

    # Load existing config if available
    if config_path.exists():
        try:
            config = _load_yaml(str(config_path))
            if config and "alerts" in config:
                return config.get("alerts", {})
        except Exception:
            pass

    # Return defaults if no config found
    return {"enabled": True, "rules": [asdict(r) for r in _get_default_rules()]}


def load_state() -> dict[str, AlertRuleState]:
    """Load alert state from ~/.tokenpak/alert_state.json.

    Returns dict mapping rule name -> AlertRuleState.
    """
    state_path = _get_state_path()

    if not state_path.exists():
        return {}

    try:
        with open(state_path, "r") as f:
            data = json.load(f)
        return {
            name: AlertRuleState(
                name=name,
                last_fired=entry.get("last_fired"),
                last_value=entry.get("last_value"),
                fired_count=entry.get("fired_count", 0),
            )
            for name, entry in data.items()
        }
    except Exception:
        return {}


def save_state(state: dict[str, AlertRuleState]):
    """Persist alert state to ~/.tokenpak/alert_state.json."""
    state_path = _get_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    data = {name: rule_state.to_dict() for name, rule_state in state.items()}
    with open(state_path, "w") as f:
        json.dump(data, f, indent=2)


def _get_proxy_stats() -> dict:
    """Fetch live stats from proxy."""
    import urllib.request as _urlreq

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}/stats", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return {}


def _get_proxy_health() -> dict:
    """Fetch health status from proxy."""
    import urllib.request as _urlreq

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return {}


def evaluate_rule(rule: AlertRule, stats: dict, health: dict) -> tuple[bool, Optional[float]]:
    """Evaluate a single alert rule against current stats.

    Args:
        rule: AlertRule to evaluate
        stats: Stats dict from proxy /stats endpoint
        health: Health dict from proxy /health endpoint

    Returns:
        (fired: bool, value: Optional[float])
    """
    cond = rule.condition

    # Parse simple conditions
    if "cache_hit_rate" in cond:
        cache_stats = _get_proxy_cache_stats() or {}
        hits = cache_stats.get("cache_hits", 0)
        misses = cache_stats.get("cache_misses", 0)
        total = hits + misses
        value = (hits / total * 100) if total > 0 else 0.0

        if "< " in cond:
            threshold = float(cond.split("< ")[1]) * 100  # Convert to percent
            return (value < threshold, value)
        elif "<=" in cond:
            threshold = float(cond.split("<= ")[1]) * 100
            return (value <= threshold, value)

    elif "error_rate" in cond:
        requests = stats.get("requests", 0)
        errors = stats.get("errors", 0)
        value = (errors / requests * 100) if requests > 0 else 0.0

        if "> " in cond:
            threshold = float(cond.split("> ")[1]) * 100  # Convert to percent
            return (value > threshold, value)
        elif ">=" in cond:
            threshold = float(cond.split(">= ")[1]) * 100
            return (value >= threshold, value)

    elif "health" in cond:
        health_status = health.get("status", "unknown")
        if "!=" in cond:
            expected = cond.split("!= ")[1].strip("'\"")
            return (health_status != expected, None)
        else:
            return (health_status == "ok", None)

    return (False, None)


def _get_proxy_cache_stats() -> dict:
    """Fetch cache stats from proxy."""
    import urllib.request as _urlreq

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}/cache-stats", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return {}


def check_alerts() -> list[tuple[AlertRule, Optional[float]]]:
    """Evaluate all alert rules and return list of fired alerts.

    Returns:
        List of (AlertRule, value) tuples for alerts that fired
    """
    config = load_config()
    if not config.get("enabled", True):
        return []

    rules_config = config.get("rules", [])
    if not rules_config:
        rules_config = [asdict(r) for r in _get_default_rules()]

    # Load current state
    state = load_state()

    # Fetch live data
    stats = _get_proxy_stats()
    health = _get_proxy_health()

    # Evaluate rules
    fired = []
    for rule_dict in rules_config:
        rule = AlertRule(**rule_dict)

        # Check if rule should fire
        should_check = True
        rule_state = state.get(rule.name)
        if rule_state:
            should_check = rule_state.should_fire(rule.cooldown_minutes)

        # Evaluate
        if should_check:
            triggered, value = evaluate_rule(rule, stats, health)
            if triggered:
                # Update state
                if not rule_state:
                    rule_state = AlertRuleState(name=rule.name)
                    state[rule.name] = rule_state
                rule_state.update_fired(value or 0.0)
                fired.append((rule, value))

    # Save updated state
    save_state(state)

    return fired
