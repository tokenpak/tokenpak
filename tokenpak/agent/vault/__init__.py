"""Backwards-compat shim — see vault.

Canonical home is ``tokenpak.vault`` (Architecture §1 — vault owns
indexing, retrieval, chunking, AST parsing, symbol extraction,
filesystem watcher, SQLite index). Moved 2026-04-20 per D1
migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.vault import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.agent.vault is deprecated — "
    "import from tokenpak.vault (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
