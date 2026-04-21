"""Canonical TIP wire headers.

Every TIP-defined header uses the ``X-TokenPak-*`` prefix. TIP headers
never alter the request body (§5.2 byte-fidelity still wins).

Phase 1 scaffold. Phase 2 fills in the typed header set and the
serialization helpers shared by ``proxy/`` and ``sdk/``.
"""

from __future__ import annotations

TIP_VERSION = "X-TokenPak-TIP-Version"
TIP_PROFILE = "X-TokenPak-Profile"
TIP_CAPABILITY = "X-TokenPak-Capability"
TIP_CACHE_ORIGIN = "X-TokenPak-Cache-Origin"
TIP_SAVINGS_TOKENS = "X-TokenPak-Savings-Tokens"
TIP_SAVINGS_COST = "X-TokenPak-Savings-Cost"
TIP_REQUEST_ID = "X-TokenPak-Request-Id"
TIP_COMPRESSION_MS = "X-TokenPak-Compression-Ms"
