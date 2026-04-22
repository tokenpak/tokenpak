"""Protocol primitives for route classification + policy.

These types are the **shared vocabulary** every subsystem uses to ask
"what kind of request is this?" and "what capabilities does it get?".

- :class:`RouteClass` — the taxonomy (claude-code-tui, anthropic-sdk, ...)
- :class:`Policy` — per-request capability flags (body_handling,
  cache_ownership, injection_enabled, ...)

No other subsystem defines its own RouteClass or Policy. Classifier lives
in ``services.routing_service.classifier``; resolver lives in
``services.policy_service.resolver``. Entrypoints (cli, sdk, companion)
consume both via ``proxy.client`` or (for §5.2-C local helpers) directly.

Architectural invariant: **Policy is the only branching signal.** No ad
hoc ``if "claude-code" in target_url`` branches outside the classifier
implementation itself.
"""

from __future__ import annotations

from tokenpak.core.routing.policy import Policy
from tokenpak.core.routing.route_class import RouteClass

__all__ = ["Policy", "RouteClass"]
