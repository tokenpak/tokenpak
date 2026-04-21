"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.main``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.main is a deprecated re-export; "
    "import from tokenpak.cli.main instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.main import *  # noqa: F401,F403,E402

__all__ = ["DB_PATH", "PROXY_BASE", "PROXY_SERVICE", "SEP", "cmd_config", "cmd_help", "cmd_last", "cmd_logs", "cmd_proxy_restart", "cmd_proxy_status", "cmd_reset", "cmd_status", "cmd_version", "fmt_c", "fmt_n", "header", "kv", "main", "proxy_err", "proxy_get", "sym"]
