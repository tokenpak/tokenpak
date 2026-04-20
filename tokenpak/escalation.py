"""Backwards-compat shim — see tokenpak.services.escalation."""
from __future__ import annotations
import warnings
from tokenpak.services.escalation import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.escalation is deprecated — import from tokenpak.services.escalation. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
