"""Wire-format-agnostic Request type crossing the services boundary.

This is the canonical input type for ``services.execute(...)``. Every
entrypoint (cli, sdk, companion, dashboard, alerts) that reaches the
execution backbone translates its native input (HTTP request, CLI argv,
framework Runnable, IDE capsule) into a ``services.Request`` and passes
it through ``proxy.client.execute``.

Phase 2 scaffold - the dataclass below is intentionally minimal. Fields
are added as pipeline extraction lands in tasks P2-01 through P2-05.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Request:
    """A single unit of LLM work crossing the services/ boundary.

    Fields are reference-implementation-driven: anything that needs to cross
    ``services/`` <-> ``proxy/`` <-> entrypoints lives here, and its type
    originates in ``tokenpak.core.contracts.*``.
    """

    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
