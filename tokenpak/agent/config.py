"""TokenPak Agent Config — persistent key/value config stored in ~/.tokenpak/config.json.

Env vars always take priority over the config file.
Config file values are read fresh on each call (no process-lifetime caching needed
since this is called only once per proxy request for the footer toggle).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.path.expanduser("~/.tokenpak/config.json"))

# Keys that map to env var overrides (env takes priority)
_ENV_OVERRIDES: dict[str, str] = {
    "stats_footer": "TOKENPAK_STATS_FOOTER",
    "metrics.enabled": "TOKENPAK_METRICS_ENABLED",
    "debug": "TOKENPAK_DEBUG",
    "capsule_builder.enabled": "TOKENPAK_CAPSULE_BUILDER",
}


def _load() -> dict[str, Any]:
    """Load config from disk, returning an empty dict if missing or corrupt."""
    try:
        return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    """Persist config to disk, creating parent dirs as needed."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


def get_config() -> dict[str, Any]:
    """Return a merged view: env var overrides take priority over file values."""
    data = _load()
    for key, env_var in _ENV_OVERRIDES.items():
        env_val = os.environ.get(env_var)
        if env_val is not None:
            data[key] = env_val not in ("0", "false", "False", "no")
    return data


def set_config(key: str, value: Any) -> None:
    """Persist a config key to file (env vars still override at read time)."""
    data = _load()
    data[key] = value
    _save(data)


def get_metrics_enabled() -> bool:
    """Return True if anonymous metrics reporting is opt-in enabled.

    Resolution order:
      1. TOKENPAK_METRICS_ENABLED env var (1/true → on)
      2. ~/.tokenpak/config.json "metrics.enabled" key
      3. Default: False (opt-in — disabled by default)
    """
    env_val = os.environ.get("TOKENPAK_METRICS_ENABLED")
    if env_val is not None:
        return env_val not in ("0", "false", "False", "no")
    data = _load()
    return bool(data.get("metrics.enabled", False))


def get_stats_footer_enabled() -> bool:
    """Return True if the stats footer should be printed after each request.

    Resolution order:
      1. TOKENPAK_STATS_FOOTER env var (1/true → on, 0/false → off)
      2. ~/.tokenpak/config.json "stats_footer" key
      3. Default: False (opt-in)
    """
    env_val = os.environ.get("TOKENPAK_STATS_FOOTER")
    if env_val is not None:
        return env_val not in ("0", "false", "False", "no")
    data = _load()
    return bool(data.get("stats_footer", False))


# ─────────────────────────────────────────────────────────────────────────────
# Capsule Builder
# ─────────────────────────────────────────────────────────────────────────────


def get_capsule_builder_enabled() -> bool:
    """Return True if capsule builder is enabled.

    Resolution order:
      1. TOKENPAK_CAPSULE_BUILDER env var (1/true → on, 0/false → off)
      2. ~/.tokenpak/config.json "capsule_builder.enabled" key
      3. Default: False (opt-in)
    """
    env_val = os.environ.get("TOKENPAK_CAPSULE_BUILDER")
    if env_val is not None:
        return env_val not in ("0", "false", "False", "no")
    data = _load()
    capsule_cfg = data.get("capsule_builder", {})
    if isinstance(capsule_cfg, dict):
        return bool(capsule_cfg.get("enabled", False))
    return bool(capsule_cfg)


def set_capsule_builder_enabled(enabled: bool) -> None:
    """Enable or disable capsule builder in config file."""
    data = _load()
    if "capsule_builder" not in data or not isinstance(data["capsule_builder"], dict):
        data["capsule_builder"] = {}
    data["capsule_builder"]["enabled"] = enabled
    _save(data)


def load_config() -> dict:
    """Return the full config dict (for direct access by other modules)."""
    return _load()


# ─────────────────────────────────────────────────────────────────────────────
# Debug Mode
# ─────────────────────────────────────────────────────────────────────────────


def get_debug_enabled() -> bool:
    """Return True if debug mode is enabled.

    Resolution order:
      1. TOKENPAK_DEBUG env var (1/true → on, 0/false → off)
      2. ~/.tokenpak/config.json "debug" key
      3. Default: False
    """
    env_val = os.environ.get("TOKENPAK_DEBUG")
    if env_val is not None:
        return env_val not in ("0", "false", "False", "no")
    data = _load()
    return bool(data.get("debug", False))


def set_debug_enabled(enabled: bool) -> None:
    """Enable or disable debug mode in config file."""
    set_config("debug", enabled)


def debug_log(message: str, **context: Any) -> None:
    """Log a debug message if debug mode is enabled.

    Context kwargs are appended as key=value pairs.
    Output goes to stderr to avoid interfering with proxy responses.
    """
    if not get_debug_enabled():
        return

    import sys
    import time

    ts = time.strftime("%H:%M:%S")
    ctx_str = " ".join(f"{k}={v}" for k, v in context.items()) if context else ""
    line = f"[DEBUG {ts}] {message}"
    if ctx_str:
        line += f" | {ctx_str}"
    print(line, file=sys.stderr)
