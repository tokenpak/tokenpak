"""TokenPak Dashboard — Alert Settings Store.

Persists alert configuration to a JSON file alongside the telemetry DB.
Thread-safe via a file lock (write-then-rename pattern).
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

DEFAULT_ALERT_CONFIG: dict[str, Any] = {
    "cost_spike": {
        "enabled": True,
        "threshold_pct": 50,
        "threshold_abs": None,
        "threshold_type": "pct",
        "severity": "warning",
        "basis_days": 7,
    },
    "savings_drop": {
        "enabled": True,
        "threshold_pct": 30,
        "severity": "warning",
    },
    "retry_spike": {
        "enabled": True,
        "threshold_pct": 20,
        "severity": "critical",
        "window": "hourly",
    },
    "latency": {
        "enabled": True,
        "metric": "p95",
        "threshold_ms": 2000,
        "severity": "warning",
    },
    "error_rate": {
        "enabled": True,
        "threshold_pct": 10,
        "severity": "warning",
    },
    "channels": {
        "in_app": True,
        "email": {
            "enabled": False,
            "address": "",
            "min_severity": "warning",
        },
        "webhook": {
            "enabled": False,
            "url": "",
        },
    },
    "quiet_hours": {
        "enabled": False,
        "start": "22:00",
        "end": "08:00",
    },
}

SEVERITY_LEVELS = ["info", "warning", "critical"]


class AlertSettings:
    """Read/write alert configuration from a JSON file."""

    def __init__(self, config_path: str | pathlib.Path) -> None:
        self._path = pathlib.Path(config_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        """Return current config, merging with defaults for missing keys."""
        if not self._path.exists():
            return dict(DEFAULT_ALERT_CONFIG)
        try:
            with open(self._path) as f:
                saved = json.load(f)
            # Deep merge: defaults win for missing keys
            return _deep_merge(DEFAULT_ALERT_CONFIG, saved)
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_ALERT_CONFIG)

    def save(self, config: dict[str, Any]) -> None:
        """Validate and persist config atomically."""
        validated = self._validate(config)
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(validated, f, indent=2)
        os.replace(tmp, self._path)

    def _validate(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """Raise ValueError on bad inputs, else return cleaned config."""

        def pct_field(val, name):
            v = float(val)
            if not (0 <= v <= 500):
                raise ValueError(f"{name} must be 0-500%")
            return round(v, 2)

        def ms_field(val, name):
            v = int(val)
            if not (0 <= v <= 60000):
                raise ValueError(f"{name} must be 0-60000 ms")
            return v

        out = _deep_merge(DEFAULT_ALERT_CONFIG, cfg)

        # Cost spike
        cs = out["cost_spike"]
        cs["threshold_pct"] = pct_field(cs.get("threshold_pct", 50), "cost_spike.threshold_pct")
        if cs.get("threshold_abs") is not None:
            v = float(cs["threshold_abs"])
            if v < 0 or v > 10000:
                raise ValueError("cost_spike.threshold_abs must be 0-10000")
            cs["threshold_abs"] = round(v, 2)
        if cs.get("severity") not in SEVERITY_LEVELS:
            raise ValueError(f"cost_spike.severity must be one of {SEVERITY_LEVELS}")

        # Savings drop
        sd = out["savings_drop"]
        sd["threshold_pct"] = pct_field(sd.get("threshold_pct", 30), "savings_drop.threshold_pct")

        # Retry spike
        rs = out["retry_spike"]
        rs["threshold_pct"] = pct_field(rs.get("threshold_pct", 20), "retry_spike.threshold_pct")

        # Latency
        lt = out["latency"]
        lt["threshold_ms"] = ms_field(lt.get("threshold_ms", 2000), "latency.threshold_ms")
        if lt.get("metric") not in ("p95", "p99"):
            raise ValueError("latency.metric must be p95 or p99")

        # Error rate
        er = out["error_rate"]
        er["threshold_pct"] = pct_field(er.get("threshold_pct", 10), "error_rate.threshold_pct")

        # Channels: email validation
        email_cfg = out["channels"]["email"]
        if email_cfg.get("enabled") and email_cfg.get("address"):
            addr = email_cfg["address"].strip()
            if "@" not in addr or "." not in addr.split("@")[-1]:
                raise ValueError("channels.email.address is not a valid email")
            email_cfg["address"] = addr
        if email_cfg.get("min_severity") not in SEVERITY_LEVELS:
            email_cfg["min_severity"] = "warning"

        return out


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
