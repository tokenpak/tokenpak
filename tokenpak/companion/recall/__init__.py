# SPDX-License-Identifier: Apache-2.0
"""Recall storage foundation (OSS Phase 1).

This package owns the local-first SQLite schema and migration runner
that the recall surface — both the OSS read paths and the Pro-tier
resolver / scoring engine in a later phase — will read from and write
to.

PR-1 scope is the storage shape only: tables, the FTS5 virtual table,
and the forward-only migration runner. No capture, no recall behavior,
no ranking, no CLI. See ``schema.py`` for the DDL and ``migrations.py``
for the runner contract.

References:
    - Standard 32 — MultiPak Pro Architecture, §5 / §6 / §9 / §13
      (Decision #9 — recall storage foundation is OSS Phase 1).
    - Standard 25 — Pro Tier Architecture, §1.1
      (TIP capabilities must land in OSS before the Pro daemon can use them).
"""

from __future__ import annotations

from tokenpak.companion.recall.schema import SCHEMA_VERSION
from tokenpak.companion.recall.store import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    RISK_FLAG_SEVERITIES,
    PakListFilters,
    PakListResult,
    PakRow,
    ReasonCodeEntry,
    RecallStore,
    RiskFlagEntry,
    default_recall_db_path,
    open_recall_store,
)

__all__ = [
    "LIST_LIMIT_DEFAULT",
    "LIST_LIMIT_MAX",
    "PakListFilters",
    "PakListResult",
    "PakRow",
    "ReasonCodeEntry",
    "RecallStore",
    "RISK_FLAG_SEVERITIES",
    "RiskFlagEntry",
    "SCHEMA_VERSION",
    "default_recall_db_path",
    "open_recall_store",
]
