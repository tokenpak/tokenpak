"""Backwards-compat shim — see compression.wire.

Canonical home is ``tokenpak.compression.wire`` (Architecture §1 —
compression pipeline generates the wire-format payload). Moved
2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.wire import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.wire is deprecated — "
    "import from tokenpak.compression.wire. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
