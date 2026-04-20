"""Cache lookup + write orchestration.

Pipeline-side gate that checks the TokenPak cache (``cache/``) before
dispatch and writes cacheable responses on the way back. Responsible for
setting ``cache_origin`` correctly (Constitution §5.3).

Phase 2 scaffold. Logic extracts from ``proxy/`` in task P2-03.
"""

from __future__ import annotations
