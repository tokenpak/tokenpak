"""DEPRECATED — `tokenpak diff` moved to tokenpak-paid (2026-04-21).

This module is a stub left behind by TPS-11. The real implementation
now lives in ``tokenpak_paid.commands._impls.diff`` and is
installed via:

    tokenpak activate YOUR-KEY
    tokenpak install-tier pro

Importing any callable from this stub and invoking it prints an
upgrade message and exits with status 2. Dynamic discovery
(``tokenpak.commands`` entry-points — TPS-01) routes ``tokenpak
diff`` to the real paid implementation when it is installed.
"""

from __future__ import annotations

import sys
import warnings as _warnings

_warnings.warn(
    "tokenpak.cli.commands.diff: implementation moved to "
    "tokenpak-paid (Pro+). Install with `tokenpak install-tier pro`. "
    "This OSS stub will be removed in tokenpak 2.0.",
    DeprecationWarning,
    stacklevel=2,
)


def _upgrade_stub(*args, **kwargs):
    """Upgrade-required stub left behind by TPS-11."""
    print(
        "⚠ The `tokenpak diff` command requires a Pro subscription.\n"
        "  Run: tokenpak activate <YOUR-KEY>\n"
        "  Then: tokenpak install-tier pro\n"
        "  (Don’t have a key? Visit tokenpak.ai/pricing.)",
        file=sys.stderr,
    )
    sys.exit(2)


# Preserve every public symbol external callers import from this module.
# Each one is aliased to _upgrade_stub so any invocation path ends in
# the same upgrade message.
run_diff_cmd = _upgrade_stub


__all__ = ["run_diff_cmd"]
