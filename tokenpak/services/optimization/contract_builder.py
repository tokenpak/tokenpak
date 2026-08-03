"""Build a per-request ``OptimizationContract``.

The contract is the upstream-contract-shaped artifact that a stage consults
to decide whether it is eligible. This compatibility builder produces a
minimal proxy-local stand-in that records the inputs and exposes a
``has(...)`` capability check. The richer TIP contract requires request,
model, cache, compression, and telemetry inputs that this legacy surface does
not receive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional


@dataclass(frozen=True)
class _LocalOptimizationContract:
    """Local fallback shape for an optimization contract.

    Exposes the tiny surface the pipeline actually needs: a set of
    capability strings and a couple of context strings (route + platform).

    The upstream contract ships a richer ``tokenpak.tip.optimization_contract.OptimizationContract``
    with cache/compression/telemetry sub-policies. The pipeline scaffolding
    treats the contract as opaque (``ctx.contract``) so callers can switch
    implementations without changes here.
    """

    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    route_class: str = ""
    platform: str = ""
    fidelity: str = "default"
    extras: Dict[str, Any] = field(default_factory=dict)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities


def _adapter_capabilities(adapter: Any) -> FrozenSet[str]:
    """Read ``capabilities`` off the format adapter, if it declares any.

    Adapters today do not yet declare capabilities (a follow-up wires that
    in); a missing or non-iterable attribute returns an empty set, matching
    the proposal's "graceful unknowns" rule.
    """
    raw = getattr(adapter, "capabilities", None)
    if raw is None:
        return frozenset()
    try:
        return frozenset(str(c) for c in raw)
    except TypeError:
        return frozenset()


def build_contract(
    *,
    adapter: Any = None,
    platform: Optional[str] = None,
    route: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    fidelity: str = "default",
    extras: Optional[Dict[str, Any]] = None,
) -> _LocalOptimizationContract:
    """Construct an OptimizationContract for the current request.

    Returns the stable ``_LocalOptimizationContract`` compatibility shape.
    """
    caps = _adapter_capabilities(adapter)
    route_class = route or ""
    platform_str = platform or ""
    extras_dict = dict(extras or {})
    if policy:
        extras_dict.setdefault("policy", dict(policy))

    # This service-level scaffold intentionally returns its stable local
    # contract. The richer TIP contract requires request/model/cache policy
    # fields that are not inputs to this compatibility builder.
    return _LocalOptimizationContract(
        capabilities=caps,
        route_class=route_class,
        platform=platform_str,
        fidelity=fidelity,
        extras=extras_dict,
    )


__all__ = ["build_contract", "_LocalOptimizationContract"]
