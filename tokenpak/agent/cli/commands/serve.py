"""tokenpak.agent.cli.commands.serve — deprecated compatibility shim.

Canonical location: tokenpak.cli.commands.serve. This module predates the
migration of the `tokenpak serve` command to tokenpak.cli.commands.serve
(which uses the current tokenpak.vault.ingest.api app factory); no code in
this codebase imports from here. Kept only for backward-compatibility with
any external caller that may still import this path directly.

Note: the canonical module no longer includes the one-time metrics opt-in
prompt (`_maybe_show_metrics_prompt`) that this shim's predecessor had;
that flow was superseded by the install-level heartbeat in
tokenpak.telemetry.install_reporter plus `tokenpak config set
metrics.enabled`. Do not add new dependents — use
tokenpak.cli.commands.serve instead.
"""

import warnings as _warnings

from tokenpak.cli.commands.serve import (  # noqa: F401
    _apply_safe_defaults,
    _default_workers,
    _maybe_show_compression_notice,
    run_serve_cmd,
)

_warnings.warn(
    "tokenpak.agent.cli.commands.serve is deprecated and unused internally; "
    "use tokenpak.cli.commands.serve instead. This module will be removed "
    "in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "run_serve_cmd",
    "_default_workers",
    "_apply_safe_defaults",
    "_maybe_show_compression_notice",
]
