"""Per-request context passed through the optimization pipeline.

Mirrors the ``OptimizationContext`` shape proposed in the TIP-First Codex
Optimization Layer proposal (Phase 3 Component B). The fields are kept
loosely typed (``Any`` where TIP-02 hasn't landed in this workspace) so the
context can be constructed without depending on contracts that aren't
imported here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .trace import OptimizationTrace


@dataclass
class OptimizationContext:
    """All state for one request flowing through the pipeline.

    request_id:      unique id (proxy-side request id)
    raw_body:        original POST body bytes — IMMUTABLE in observe-only
    canonical:       optional CanonicalRequest from the format adapter
                     (may be None when adapter normalization isn't safe)
    adapter:         the FormatAdapter instance, when known
    platform:        platform string (from sdk.registry.detect_platform())
    route:           route-class string from _classify_route()
    policy:          route policy dict from get_policy(route)
    contract:        OptimizationContract (TIP-02) — opaque to the pipeline
    headers:         outbound headers dict (case as received)
    target_url:      upstream URL the proxy will eventually call
    trace:           pipeline trace, written to as stages run

    The dataclass holds Optional types because not every call site has all
    of them; the pipeline is defensive about None.
    """

    request_id: str
    raw_body: bytes
    trace: OptimizationTrace
    canonical: Any = None
    adapter: Any = None
    platform: Optional[str] = None
    route: Optional[str] = None
    policy: Dict[str, Any] = field(default_factory=dict)
    contract: Any = None
    headers: Dict[str, str] = field(default_factory=dict)
    target_url: str = ""

    @property
    def body_size(self) -> int:
        return len(self.raw_body) if self.raw_body else 0
