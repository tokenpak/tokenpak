# SPDX-License-Identifier: MIT
"""Compression Calibration for TokenPak.

Tracks retry/success events per risk_class and auto-adjusts compression
modes when a class's retry rate exceeds threshold. Persists state to
.tokenpak/calibration.json.

Mode hierarchy (can only downgrade, never upgrade automatically):
  AGGRESSIVE → HYBRID → STRICT
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CALIBRATION_PATH = ".tokenpak/calibration.json"

# Rolling window: keep last N events total
MAX_EVENTS = 100

# Auto-downgrade threshold
RETRY_RATE_THRESHOLD = 0.20  # 20%

# Time-decay cutoffs
DECAY_7D_WEIGHT = 0.50
DECAY_14D_WEIGHT = 0.25
DECAY_30D_CUTOFF = 30  # days — events older than this are dropped

# Mode ordering (lower index = more aggressive)
_MODE_ORDER = ["aggressive", "hybrid", "strict"]


# ---------------------------------------------------------------------------
# Calibration store helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: str) -> dict:
    """Load calibration.json; return fresh structure if missing/corrupt."""
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                data.setdefault("overrides", {})
                data.setdefault("events", [])
                data.setdefault("updated", _now_iso())
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"overrides": {}, "events": [], "updated": _now_iso()}


def _save(data: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now_iso()
    p.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Event age helpers
# ---------------------------------------------------------------------------


def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp to datetime (UTC-aware)."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _age_days(ts: str, now: datetime) -> Optional[float]:
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 86400.0


def _event_weight(age_days: float) -> float:
    """Return decay weight for an event of a given age (in days)."""
    if age_days > DECAY_30D_CUTOFF:
        return 0.0  # Drop
    if age_days > 14:
        return DECAY_14D_WEIGHT
    if age_days > 7:
        return DECAY_7D_WEIGHT
    return 1.0


# ---------------------------------------------------------------------------
# Core logging API
# ---------------------------------------------------------------------------


def log_retry(
    query: str,
    mode: str,
    risk_classes_in_context: List[str],
    calibration_path: str = DEFAULT_CALIBRATION_PATH,
) -> dict:
    """
    Log a retry event (LLM needed to regenerate due to bad context).

    Args:
        query:                  The original query.
        mode:                   Compression mode used (aggressive/hybrid/strict).
        risk_classes_in_context: List of risk_class strings in context (e.g. ["CODE", "NARRATIVE"]).
        calibration_path:       Path to calibration.json store.

    Returns:
        Updated calibration data (post auto-downgrade check).
    """
    data = _load(calibration_path)
    event = {
        "type": "retry",
        "query": query,
        "mode": mode.lower(),
        "risk_classes": [rc.upper() for rc in risk_classes_in_context],
        "timestamp": _now_iso(),
    }
    data["events"].append(event)
    # Trim to rolling window
    data["events"] = data["events"][-MAX_EVENTS:]
    # Recompute overrides after new event
    _recompute_overrides(data)
    _save(data, calibration_path)
    return data


def log_success(
    query: str,
    mode: str,
    calibration_path: str = DEFAULT_CALIBRATION_PATH,
) -> dict:
    """
    Log a success event (LLM produced a good response).

    Args:
        query:              The original query.
        mode:               Compression mode used.
        calibration_path:   Path to calibration.json store.

    Returns:
        Updated calibration data.
    """
    data = _load(calibration_path)
    event = {
        "type": "success",
        "query": query,
        "mode": mode.lower(),
        "risk_classes": [],  # Not tracked per-class for successes
        "timestamp": _now_iso(),
    }
    data["events"].append(event)
    data["events"] = data["events"][-MAX_EVENTS:]
    # Recompute so that a surge of successes can clear a stale override
    _recompute_overrides(data)
    _save(data, calibration_path)
    return data


# ---------------------------------------------------------------------------
# Retry rate computation
# ---------------------------------------------------------------------------


def compute_retry_rate(
    risk_class: str,
    mode: str,
    events: List[dict],
    now: Optional[datetime] = None,
) -> float:
    """
    Compute the time-decay-weighted retry rate for a (risk_class, mode) pair.

    Only retry events with the given risk_class + mode count as "retry".
    All events (retry + success) with the given mode count as "total"
    — but only retry events carry the risk_class tag, so we measure:

        retry_weight_sum / (retry_weight_sum + success_weight_sum)

    where:
    - retry_weight_sum = sum of weights for retry events involving risk_class in mode
    - success_weight_sum = sum of weights for success events in mode (shared denominator)

    If no relevant events exist, returns 0.0.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    risk_class = risk_class.upper()
    mode = mode.lower()

    retry_w = 0.0
    success_w = 0.0

    for ev in events:
        age = _age_days(ev.get("timestamp", ""), now)
        if age is None:
            continue
        w = _event_weight(age)
        if w == 0.0:
            continue

        ev_mode = ev.get("mode", "").lower()
        ev_type = ev.get("type", "")

        if ev_mode != mode:
            continue

        if ev_type == "retry":
            ev_classes = [rc.upper() for rc in ev.get("risk_classes", [])]
            if risk_class in ev_classes:
                retry_w += w
        elif ev_type == "success":
            success_w += w

    total = retry_w + success_w
    if total == 0.0:
        return 0.0
    return retry_w / total


# ---------------------------------------------------------------------------
# Auto-downgrade logic
# ---------------------------------------------------------------------------


def _downgrade_mode(current_mode: str) -> Optional[str]:
    """
    Return the next-lower mode, or None if already at STRICT.
    Input/output are lowercase.
    """
    try:
        idx = _MODE_ORDER.index(current_mode.lower())
    except ValueError:
        return None
    if idx >= len(_MODE_ORDER) - 1:
        return None  # Already at STRICT
    return _MODE_ORDER[idx + 1]


def _recompute_overrides(data: dict, now: Optional[datetime] = None) -> None:
    """
    Fully recompute overrides from current event window.

    Replaces data["overrides"] entirely so that overrides are cleared when
    the retry rate drops back below threshold (e.g. after many successes
    are logged following an earlier retry spike).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    events = data.get("events", [])

    # Collect all risk_classes mentioned in any retry event
    risk_classes = set()
    for ev in events:
        if ev.get("type") == "retry":
            for rc in ev.get("risk_classes", []):
                risk_classes.add(rc.upper())

    new_overrides: dict = {}

    for rc in risk_classes:
        # Walk the mode chain and find the strictest override warranted
        effective_override: Optional[str] = None
        for check_mode in _MODE_ORDER[:-1]:  # STRICT can't be downgraded further
            rate = compute_retry_rate(rc, check_mode, events, now)
            if rate > RETRY_RATE_THRESHOLD:
                candidate = _downgrade_mode(check_mode)
                if candidate:
                    if effective_override is None or _MODE_ORDER.index(
                        candidate
                    ) > _MODE_ORDER.index(effective_override):
                        effective_override = candidate

        if effective_override is not None:
            new_overrides[rc] = effective_override

    # Full replacement — removes stale overrides whose rate has recovered
    data["overrides"] = new_overrides


# ---------------------------------------------------------------------------
# Public API: get_effective_mode
# ---------------------------------------------------------------------------


def get_effective_mode(
    base_mode: str,
    risk_class: str,
    calibration_path: str = DEFAULT_CALIBRATION_PATH,
) -> str:
    """
    Return the calibrated compression mode for a given (base_mode, risk_class).

    If an override exists for risk_class AND it is stricter than base_mode,
    the override wins. Otherwise base_mode is returned unchanged.

    Args:
        base_mode:          Requested mode (aggressive/hybrid/strict).
        risk_class:         Risk class of the content (e.g. "CODE", "NARRATIVE").
        calibration_path:   Path to calibration.json store.

    Returns:
        Effective mode string (lowercase).
    """
    data = _load(calibration_path)
    base = base_mode.lower()
    override = data["overrides"].get(risk_class.upper())

    if override is None:
        return base

    # Use whichever is stricter (higher index in _MODE_ORDER)
    try:
        base_idx = _MODE_ORDER.index(base)
        override_idx = _MODE_ORDER.index(override.lower())
        return _MODE_ORDER[max(base_idx, override_idx)]
    except ValueError:
        return base


# ---------------------------------------------------------------------------
# Utility: load raw calibration data (for inspection / tests)
# ---------------------------------------------------------------------------


def load_calibration(path: str = DEFAULT_CALIBRATION_PATH) -> dict:
    """Load calibration.json and return raw dict."""
    return _load(path)
