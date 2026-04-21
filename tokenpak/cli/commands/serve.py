"""DEPRECATED — `tokenpak serve` moved to tokenpak-paid (2026-04-21).

This module is a stub left behind by TPS-11. The real implementation
now lives in ``tokenpak_paid.commands._impls.serve`` and is
installed via:

    tokenpak activate YOUR-KEY
    tokenpak install-tier team

Importing any callable from this stub and invoking it prints an
upgrade message and exits with status 2. Dynamic discovery
(``tokenpak.commands`` entry-points — TPS-01) routes ``tokenpak
serve`` to the real paid implementation when it is installed.
"""

from __future__ import annotations

import sys
import warnings as _warnings

_warnings.warn(
    "tokenpak.cli.commands.serve: implementation moved to "
    "tokenpak-paid (Team+). Install with `tokenpak install-tier team`. "
    "This OSS stub will be removed in tokenpak 2.0.",
    DeprecationWarning,
    stacklevel=2,
)


def _upgrade_stub(*args, **kwargs):
    """Upgrade-required stub left behind by TPS-11."""
    print(
        "⚠ The `tokenpak serve` command requires a Team subscription.\n"
        "  Run: tokenpak activate <YOUR-KEY>\n"
        "  Then: tokenpak install-tier team\n"
        "  (Don’t have a key? Visit tokenpak.ai/pricing.)",
        file=sys.stderr,
    )
    sys.exit(2)


# Preserve every public symbol external callers import from this module.
# Each one is aliased to _upgrade_stub so any invocation path ends in
# the same upgrade message.
run_serve_cmd = _upgrade_stub
_default_workers = _upgrade_stub


__all__ = ["run_serve_cmd", "_default_workers"]
