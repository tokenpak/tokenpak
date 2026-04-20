"""Backwards-compat shim — see compression.validation_gate."""
from __future__ import annotations
import warnings
from tokenpak.compression.validation_gate import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.validation_gate is deprecated — import from tokenpak.compression.validation_gate. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
