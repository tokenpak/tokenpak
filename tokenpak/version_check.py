"""tokenpak.version_check — backward-compat shim.

All functions have moved to tokenpak.core.version_check.
"""

from tokenpak.core.version_check import *  # noqa: F401, F403
from tokenpak.core.version_check import (  # noqa: F401
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
