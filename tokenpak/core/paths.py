# SPDX-License-Identifier: Apache-2.0
"""Centralized path resolution for tokenpak databases and config files.

All modules that need to locate telemetry.db or monitor.db should import
``get_db_path`` from here instead of hardcoding paths. This module is the
single resolver for ``telemetry.db`` — every open of that file must route
through here so writers and readers always agree on one location.

For ``monitor.db``, resolution delegates to ``tokenpak._paths.monitor_db()``
which implements the full home-directory resolution order including env var
compatibility (``TOKENPAK_DB`` / ``TOKENPAK_MONITOR_DB``), canonical
``~/.tpk/``, and legacy ``~/.tokenpak/`` paths.

For other databases (e.g. ``telemetry.db``), the original resolution order
is preserved:
  1. Environment variable ``TOKENPAK_{NAME}`` (e.g. TOKENPAK_TELEMETRY_DB)
  2. ``~/.tokenpak/{name}`` if it exists
  3. Repo-root ``{name}`` if it exists
  4. ``~/.tokenpak/{name}`` as default (even if not yet created)

For ``telemetry.db`` the legacy ``TOKENPAK_DB_PATH`` env var is honored as a
deprecated alias of ``TOKENPAK_TELEMETRY_DB`` (some older callers exported
only the former). ``TOKENPAK_TELEMETRY_DB`` wins when both are set.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo root = 3 levels up from tokenpak/core/paths.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Deprecated env-var aliases, keyed by canonical env var. Kept for
# backward compatibility; new code must set the canonical variable.
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "TOKENPAK_TELEMETRY_DB": ("TOKENPAK_DB_PATH",),
}

_warned_aliases: set[str] = set()


def _env_lookup(env_key: str) -> str | None:
    """Return the value for *env_key*, falling back to deprecated aliases."""
    value = os.environ.get(env_key)
    if value:
        return value
    for alias in _ENV_ALIASES.get(env_key, ()):
        value = os.environ.get(alias)
        if value:
            if alias not in _warned_aliases:
                _warned_aliases.add(alias)
                logger.warning(
                    "env var %s is deprecated; use %s instead", alias, env_key
                )
            return value
    return None


def get_db_path(name: str = "monitor.db") -> Path:
    """Resolve a database file path with consistent precedence.

    Always returns a Path (never None) to preserve the existing contract
    for all callers.
    """
    if name == "monitor.db":
        from tokenpak._paths import home as _home
        from tokenpak._paths import monitor_db as _monitor_db

        result = _monitor_db(mode="read")
        if result is not None:
            return result
        return _home() / "monitor.db"

    env_key = "TOKENPAK_" + name.upper().replace(".", "_").replace("-", "_")
    if p := _env_lookup(env_key):
        return Path(p).expanduser()
    dot_dir = Path.home() / ".tokenpak" / name
    if dot_dir.exists():
        return dot_dir
    repo_path = _REPO_ROOT / name
    if repo_path.exists():
        return repo_path
    return dot_dir
