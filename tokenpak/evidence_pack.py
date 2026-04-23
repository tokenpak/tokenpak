"""Backwards-compat shim — see compression.evidence_pack."""
from __future__ import annotations

import warnings

from tokenpak.compression.evidence_pack import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.evidence_pack is deprecated — import from tokenpak.compression.evidence_pack. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
