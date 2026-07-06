"""tokenpak/dashboard/settings_persistence.py — Atomic read/write for tokenpak.env.

Settings UI persistence layer. Reads the env file, validates
incoming values, and writes atomically (tmp + os.replace) with a timestamped
backup before every write.

Exception note: direct env-file write is a local-only administrative operation
(no proxy request, no pipeline involvement). Calling proxy.client is not
applicable here.

Settings take effect after the proxy is reloaded. A SIGHUP is attempted
automatically; some settings require a full service restart.
"""

from __future__ import annotations

import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tokenpak import _paths

# Default env-file location (loaded by systemd EnvironmentFile=).
ENV_FILE_PATH = Path.home() / ".config" / "tokenpak.env"

# Settings the proxy's SIGHUP handler can pick up without a full restart.
_SIGHUP_RELOADABLE = frozenset({"TOKENPAK_MODE"})


def _proxy_pid_path() -> Path:
    return _paths.under("proxy.pid")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse key=value pairs from a tokenpak.env file.

    Lines starting with # or that are blank are silently skipped.
    Returns a dict of {KEY: VALUE}; duplicate keys keep the last value.
    """
    p = path or ENV_FILE_PATH
    result: dict[str, str] = {}
    if not p.exists():
        return result
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def get_setting(key: str, default: str = "", path: Path | None = None) -> str:
    """Return a setting value from env file, with os.environ override."""
    env_val = os.environ.get(key)
    if env_val is not None:
        return env_val
    return read_env_file(path).get(key, default)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_BOOL_VALUES = frozenset({"0", "1", "true", "false", "yes", "no", "on", "off"})
_PROFILES = frozenset({
    "claude-code-cli", "claude-code-tui", "claude-code-tmux",
    "claude-code-sdk", "claude-code-ide", "claude-code-cron",
})

# Local-admin writes only. Sensitive credentials, provider/remote endpoints,
# and remote alert destinations must not be created or updated through the
# dashboard settings route without an explicit architecture exception.
_FORBIDDEN_SETTINGS_WRITES = frozenset({
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "TOKENPAK_CACHE_ALERT_SLACK_CHANNEL",
    "TOKENPAK_CACHE_ALERT_WEBHOOK_URL",
    "TOKENPAK_OLLAMA_UPSTREAM",
    "TOKENPAK_REMOTE_HOST",
})


def _validate_bool(key: str, value: str) -> None:
    if value.lower() not in _BOOL_VALUES:
        raise ValueError(f"{key}: expected boolean (0/1/true/false), got {value!r}")


def _validate_positive_int(key: str, value: str) -> None:
    try:
        n = int(value)
    except ValueError:
        raise ValueError(f"{key}: expected integer, got {value!r}")
    if n < 0:
        raise ValueError(f"{key}: must be ≥ 0, got {n}")


def _validate_positive_float(key: str, value: str) -> None:
    try:
        f = float(value)
    except ValueError:
        raise ValueError(f"{key}: expected float, got {value!r}")
    if f < 0:
        raise ValueError(f"{key}: must be ≥ 0, got {f}")


def _validate_pct(key: str, value: str) -> None:
    try:
        f = float(value)
    except ValueError:
        raise ValueError(f"{key}: expected float (0–100), got {value!r}")
    if not (0 <= f <= 100):
        raise ValueError(f"{key}: must be 0–100, got {f}")


_VALIDATORS: dict[str, Any] = {
    "TOKENPAK_ACTIVE_PROFILE": lambda k, v: None if v in _PROFILES else (_ for _ in ()).throw(ValueError(f"{k}: unknown profile {v!r}")),
    "TOKENPAK_VAULT_INJECT_ENABLED": _validate_bool,
    "TOKENPAK_INJECT_BUDGET":        _validate_positive_int,
    "TOKENPAK_INJECT_TOP_K":         _validate_positive_int,
    "TOKENPAK_INJECT_MIN_SCORE":     _validate_positive_float,
    "TOKENPAK_BUDGET_CONTROLLER":    _validate_bool,
    "TOKENPAK_BUDGET_TOTAL":         _validate_positive_int,
    "TOKENPAK_CACHE_ALERT_WEBHOOK_ENABLED": _validate_bool,
    "TOKENPAK_CACHE_ALERT_THRESHOLD":       _validate_pct,
    "TOKENPAK_LOCAL_FIRST_ROUTING":  _validate_bool,
}


def validate_settings(updates: dict[str, str]) -> list[str]:
    """Return a list of validation error messages (empty if all valid)."""
    errors: list[str] = []
    for key, value in updates.items():
        if key in _FORBIDDEN_SETTINGS_WRITES:
            errors.append(f"{key}: dashboard writes are disabled; edit tokenpak.env manually or request a webhook exception")
            continue
        validator = _VALIDATORS.get(key)
        if validator is None:
            continue
        try:
            validator(key, value)
        except ValueError as e:
            errors.append(str(e))
    return errors


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _backup_env_file(p: Path) -> Path | None:
    """Copy current env file to a timestamped .bak path. Returns bak path."""
    if not p.exists():
        return None
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = p.with_suffix(f".env.bak.{ts}")
    bak.write_bytes(p.read_bytes())
    return bak


def _merge_env_lines(existing: list[str], updates: dict[str, str]) -> list[str]:
    """Apply updates to existing env-file lines, preserving comments and order.

    Keys in *updates* that already appear are updated in place; new keys are
    appended at the end (before any trailing blank line).
    """
    updated_keys: set[str] = set()
    result: list[str] = []
    for raw in existing:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or "=" not in stripped:
            result.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            result.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            result.append(line)

    # Append new keys that weren't in the file yet.
    new_keys = [k for k in updates if k not in updated_keys]
    if new_keys:
        if result and result[-1].strip():
            result.append("")
        for key in new_keys:
            result.append(f"{key}={updates[key]}")

    return result


def write_settings(
    updates: dict[str, str],
    path: Path | None = None,
    *,
    skip_validation: bool = False,
) -> tuple[bool, list[str]]:
    """Persist *updates* to the env file atomically.

    Returns ``(success, errors)``. On validation failure returns
    ``(False, [error_messages])``. On success attempts a SIGHUP to the
    running proxy for live-reloadable settings.
    """
    forbidden = [key for key in updates if key in _FORBIDDEN_SETTINGS_WRITES]
    if forbidden:
        errors = [
            f"{key}: dashboard writes are disabled; edit tokenpak.env manually or request a webhook exception"
            for key in forbidden
        ]
        return False, errors

    if not skip_validation:
        errors = validate_settings(updates)
        if errors:
            return False, errors

    p = path or ENV_FILE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    merged = _merge_env_lines(existing_lines, updates)
    content = "\n".join(merged) + "\n"

    _backup_env_file(p)

    tmp = p.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)

    _try_sighup_proxy(updates)
    return True, []


def _try_sighup_proxy(updates: dict[str, str]) -> None:
    """Send SIGHUP to the proxy if at least one updated key is hot-reloadable."""
    if not any(k in _SIGHUP_RELOADABLE for k in updates):
        return
    pid_path = _proxy_pid_path()
    if not pid_path.exists():
        return
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGHUP)
    except (ValueError, ProcessLookupError, PermissionError):
        pass


# ---------------------------------------------------------------------------
# Current-state helper for the settings page context
# ---------------------------------------------------------------------------

def load_settings_context(path: Path | None = None) -> dict[str, Any]:
    """Return a dict of current setting values for use in Jinja2 templates."""
    env = read_env_file(path)

    def _b(key: str, default: str = "0") -> bool:
        v = os.environ.get(key) or env.get(key, default)
        return v.lower() in {"1", "true", "yes", "on"}

    def _s(key: str, default: str = "") -> str:
        return os.environ.get(key) or env.get(key, default)

    def _i(key: str, default: int = 0) -> int:
        raw = os.environ.get(key) or env.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _f(key: str, default: float = 0.0) -> float:
        raw = os.environ.get(key) or env.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    return {
        # Active profile
        "active_profile": _s("TOKENPAK_ACTIVE_PROFILE") or _s("TOKENPAK_PROFILE", "claude-code-cli"),
        "available_profiles": sorted(_PROFILES),
        # Vault injection
        "vault_inject_enabled": _b("TOKENPAK_VAULT_INJECT_ENABLED", "1"),
        "inject_budget":        _i("TOKENPAK_INJECT_BUDGET", 4000),
        "inject_top_k":         _i("TOKENPAK_INJECT_TOP_K", 5),
        "inject_min_score":     _f("TOKENPAK_INJECT_MIN_SCORE", 2.0),
        # Budget enforcement
        "budget_controller_enabled": _b("TOKENPAK_BUDGET_CONTROLLER", "1"),
        "budget_total":              _i("TOKENPAK_BUDGET_TOTAL", 12000),
        # Cache invalidation alerts
        "cache_alert_webhook_enabled": _b("TOKENPAK_CACHE_ALERT_WEBHOOK_ENABLED", "0"),
        "cache_alert_slack_channel":   _s("TOKENPAK_CACHE_ALERT_SLACK_CHANNEL"),
        "cache_alert_threshold":       _f("TOKENPAK_CACHE_ALERT_THRESHOLD", 50.0),
        # Local-first routing
        "local_first_routing_enabled": _b("TOKENPAK_LOCAL_FIRST_ROUTING", "0"),
        "ollama_upstream":             _s("TOKENPAK_OLLAMA_UPSTREAM", "http://localhost:11434"),
        # Compliance routing
        "compliance_provider": _s("TOKENPAK_COMPLIANCE_PROVIDER"),
        # Meta
        "env_file_path": str(path or ENV_FILE_PATH),
        "env_file_exists": (path or ENV_FILE_PATH).exists(),
        "proxy_pid": _get_proxy_pid(),
    }


def _get_proxy_pid() -> int | None:
    pid_path = _proxy_pid_path()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None
