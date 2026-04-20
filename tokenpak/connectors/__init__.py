"""Backwards-compat shim — see sources.

Canonical home is ``tokenpak.sources`` (Architecture §1 — "External
and local knowledge connectors: local filesystem, GitHub, Notion,
Drive, shared/org sources, sync pipelines"). Moved 2026-04-20 per
D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.sources import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.connectors is deprecated — "
    "import from tokenpak.sources (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
