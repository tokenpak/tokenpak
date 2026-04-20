"""Canonical TIP error codes and shapes.

TIP errors are always JSON objects — the proxy never emits bare HTML
404s (see ``project_openclaw_cali_auth_failure_mode``). Schema is in
the registry at ``schemas/tip/error.schema.json``.

Phase 1 scaffold. Phase 2 populates the ``TIPError`` class + code
enum + helpers shared by ``proxy/``, ``services/``, and ``sdk/``.
"""

from __future__ import annotations
