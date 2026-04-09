"""tokenpak.version_check — backward-compat shim.

All functions have moved to tokenpak.infrastructure.version_check.
"""

from tokenpak.infrastructure.version_check import *  # noqa: F401, F403
from tokenpak.infrastructure.version_check import (  # noqa: F401
    run_startup_check,
    _compute_config_hash,
    _query_proxy_version,
    _load_config,
    _load_lock,
    _log_warning,
)

__all__ = [
    "run_startup_check",
    "_compute_config_hash",
    "_query_proxy_version",
    "_load_config",
    "_load_lock",
    "_log_warning",
]
