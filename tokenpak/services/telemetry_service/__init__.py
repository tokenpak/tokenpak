"""Per-request telemetry emission.

Single site that writes one row to the telemetry store
(``~/.tokenpak/telemetry.db``) per request that exits the services
pipeline. Enforces the Architecture §7.1 rule that only ``services/``
writes to the authoritative measurement ledger.

Phase 2 scaffold. Writer extracts from ``proxy/`` in task P2-05.
"""

from __future__ import annotations
