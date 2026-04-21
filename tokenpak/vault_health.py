"""Backwards-compat shim — see telemetry.vault_health."""
from __future__ import annotations
import warnings
from tokenpak.telemetry.vault_health import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.vault_health is deprecated — import from tokenpak.telemetry.vault_health. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
