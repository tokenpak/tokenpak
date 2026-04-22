"""Per-mode dashboard panels.

Each panel reads from ``monitor.db`` (via
``tokenpak.proxy.client``) and groups rows by ``route_class`` so
operators can see per-mode activity (``claude-code-tui`` vs
``anthropic-sdk`` vs ``generic``) without building bespoke queries.

Dashboard UI is an entrypoint per Architecture §5.2 — panels consume
shared services via read-only queries. No pipeline execution runs
here.
"""

from __future__ import annotations

from tokenpak.dashboard.panels.per_mode import PerModePanel

__all__ = ["PerModePanel"]
