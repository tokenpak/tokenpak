"""DEPRECATED — `tokenpak trigger` body moved to tokenpak-paid (TPS-11).

This file used to hold the full trigger CLI group. It is now a stub;
the real implementation lives at
``tokenpak_paid.commands._impls.trigger`` and ships with a Team or
Enterprise subscription.
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.cli.trigger_cmd: implementation moved to tokenpak-paid "
    "(Team+). Install with `tokenpak install-tier team`. This OSS "
    "stub will be removed in tokenpak 2.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export the stubbed symbols from the command module so existing
# importers (``from tokenpak.cli.trigger_cmd import trigger_group``)
# still see the upgrade-stub callable.
from tokenpak.cli.commands.trigger import (  # noqa: F401
    add_cmd,
    list_cmd,
    log_cmd,
    remove_cmd,
    test_cmd,
    trigger_group,
)

__all__ = ["trigger_group", "list_cmd", "add_cmd", "remove_cmd",
           "test_cmd", "log_cmd"]
