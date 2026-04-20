"""Backwards-compat shim — see routing.routing_ledger."""
from __future__ import annotations
import warnings
from tokenpak.routing.routing_ledger import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.routing_ledger is deprecated — import from tokenpak.routing.routing_ledger. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
