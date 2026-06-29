"""TokenPak Dispatch Run Ledger (P-LEDGER-01).

The Run Ledger is the durable SQLite store for the ten Dispatch execution
record classes produced during a run. It lives under
the canonical TokenPak home — ``~/.tpk/dispatch/runs.db``, resolved via
:func:`tokenpak._paths.under` — and never writes into the project repo.

Public surface:

* :class:`RunLedger` — open the ledger, write/read each record class, and drive
  the DispatchEffect ``planned → applied/failed`` lifecycle plus the
  ``select_dangling_planned_effects`` resume-reconciliation query.
* :func:`ledger_db_path` — the resolved on-disk path of the ledger DB.
* :data:`SCHEMA_VERSION`, :func:`migrate` — the versioned, idempotent schema
  migration ladder.

The Run Ledger stores Dispatch execution records ONLY; it does not promote any
record to a canonical Pak type (the Pak taxonomy boundary).
"""

from __future__ import annotations

# Pydantic is the contract layer for every Dispatch record. Guard the import at
# this package boundary so a slim install (without the opt-in ``dispatch``
# extra) fails with an actionable hint rather than a raw ImportError.
try:
    import pydantic as _pydantic  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised only on slim installs
    raise ImportError(
        "TokenPak Dispatch requires pydantic. Install the dispatch extra: "
        "`pip install tokenpak[dispatch]`."
    ) from exc

from .db import RunLedger, ledger_db_path
from .migrations import SCHEMA_VERSION, get_current_schema_version, migrate

__all__ = [
    "RunLedger",
    "ledger_db_path",
    "SCHEMA_VERSION",
    "get_current_schema_version",
    "migrate",
]
