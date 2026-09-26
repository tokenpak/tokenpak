"""tokenpak.agent.cli.commands.metrics — deprecated compatibility shim.

Canonical location: tokenpak.cli.commands.metrics. This module predates the
migration of the `tokenpak metrics` command group; no code in this codebase
imports from here. Kept only for backward-compatibility with any external
caller that may still import this path directly.

Note: the canonical module dropped the `cmd_on`/`cmd_off` (and the click
"on"/"off" subcommands) that this shim's predecessor had — enabling and
disabling metrics is now done via `tokenpak config set metrics.enabled
<true|false>` instead of a dedicated subcommand. Do not add new
dependents — use tokenpak.cli.commands.metrics instead.
"""

import warnings as _warnings

from tokenpak.cli.commands.metrics import (  # noqa: F401
    SEP,
    _fmt_ratio,
    _fmt_tokens,
    _metrics_enabled,
    cmd_history,
    cmd_preview,
    cmd_status,
    cmd_sync,
)

_warnings.warn(
    "tokenpak.agent.cli.commands.metrics is deprecated and unused internally; "
    "use tokenpak.cli.commands.metrics instead. This module will be removed "
    "in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "cmd_status",
    "cmd_preview",
    "cmd_history",
    "cmd_sync",
    "SEP",
    "_metrics_enabled",
    "_fmt_ratio",
    "_fmt_tokens",
]

try:
    from tokenpak.cli.commands.metrics import metrics_cmd  # noqa: F401

    __all__.append("metrics_cmd")
except ImportError:
    pass
