"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.failover``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.failover is a deprecated re-export; "
    "import from tokenpak.proxy.failover instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.failover import *  # noqa: F401,F403,E402

__all__ = ["DEFAULT_YAML_TEMPLATE", "Dict", "FailoverConfig", "FailoverManager", "FailoverResult", "Iterator", "List", "Optional", "Path", "ProviderEntry", "dataclass", "field", "load_failover_config", "logger", "write_default_config"]
