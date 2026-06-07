# SPDX-License-Identifier: Apache-2.0
"""TIP optimization contract — top-level per-request optimization specification.

``OptimizationContract`` aggregates all per-request policy decisions into
a single object that the proxy optimization pipeline reads to determine which
stages to apply, at what settings, and with what fidelity constraints.

It is built once per request (by Component B's contract_builder)
from:
- The resolved ``FormatAdapter`` (format capabilities)
- The detected platform adapter (platform hints)
- The classified ``OptimizationRouteClass`` (content semantics)
- Any explicit caller-supplied policy overrides

No adapter, proxy stage, or downstream consumer should modify this object
after construction — treat it as immutable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from tokenpak.tip.cache_contract import CachePolicy
from tokenpak.tip.compression_contract import CompressionPolicy
from tokenpak.tip.fidelity_contract import FidelityPolicy
from tokenpak.tip.route_contract import OptimizationRouteClass
from tokenpak.tip.telemetry_contract import TelemetryPolicy


@dataclass
class OptimizationContract:
    """Per-request optimization specification.

    Fields:
    - ``request_id``: unique identifier for this request (for trace correlation).
    - ``adapter_format``: the format adapter's ``source_format`` string.
    - ``platform``: platform identifier from platform adapter, or None.
    - ``model``: model identifier as seen in the canonical request.
    - ``capabilities``: frozenset of TIP capability labels declared by
      the format adapter (from ``FormatAdapter.capabilities``).
    - ``route_class``: semantic request type (from content classification).
    - ``fidelity_policy``: content preservation constraint for this request.
    - ``cache_policy``: cache behavior contract.
    - ``compression_policy``: compression behavior contract.
    - ``telemetry_policy``: telemetry and attribution contract.
    - ``safety_flags``: list of safety signal identifiers that constrain
      what the pipeline may do (e.g. ``["dlp_redaction_required"]``).

    Invariants enforced at construction:
    - ``route_class`` must be a valid ``OptimizationRouteClass`` value.
    - ``fidelity_policy`` must be a valid ``FidelityPolicy`` value.
    - Adapters that do not declare ``TIP_CACHE_PROXY_MANAGED`` will have
      ``cache_policy.enabled`` forced to False by the contract builder.
      (This enforcement happens in proxy/optimization/contract_builder.py,
      not here — the contract itself is a value object.)
    """

    request_id: str
    adapter_format: str
    model: str
    capabilities: Set[str] = field(default_factory=set)
    route_class: OptimizationRouteClass = OptimizationRouteClass.UNKNOWN
    fidelity_policy: FidelityPolicy = FidelityPolicy.LOSSLESS_REQUIRED
    cache_policy: CachePolicy = field(default_factory=CachePolicy)
    compression_policy: CompressionPolicy = field(default_factory=CompressionPolicy)
    telemetry_policy: TelemetryPolicy = field(default_factory=TelemetryPolicy)
    platform: Optional[str] = None
    safety_flags: List[str] = field(default_factory=list)

    def has_capability(self, capability: str) -> bool:
        """True when the adapter declared this TIP capability label."""
        return capability in self.capabilities

    def is_optimization_eligible(self) -> bool:
        """True when at least one optimization stage could run for this request.

        This is a quick pre-flight check; each stage still gates on its own
        required capabilities. Returns False only when the fidelity policy
        or safety flags universally prohibit all optimization.
        """
        if self.fidelity_policy == FidelityPolicy.NO_OPTIMIZE:
            return False
        if "no_optimize" in self.safety_flags:
            return False
        return True

    def effective_route_class_str(self) -> str:
        return self.route_class.value if isinstance(self.route_class, OptimizationRouteClass) else str(self.route_class)

    def effective_fidelity_str(self) -> str:
        return self.fidelity_policy.value if isinstance(self.fidelity_policy, FidelityPolicy) else str(self.fidelity_policy)


__all__ = ["OptimizationContract"]
