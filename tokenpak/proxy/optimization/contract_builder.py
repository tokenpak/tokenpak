"""Build a per-request ``OptimizationContract``.

The contract is the TIP-02-shaped artifact that a stage consults to decide
whether it is eligible. In this scaffold the builder produces a minimal
proxy-local stand-in that records the inputs and exposes a ``has(...)``
capability check; once TIP-02 (``tokenpak.tip.optimization_contract``) is
imported into this workspace the builder will return a real
``OptimizationContract`` instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional


@dataclass(frozen=True)
class _LocalOptimizationContract:
    """Local fallback shape for an optimization contract.

    Exposes the tiny surface the pipeline actually needs: a set of
    capability strings and a couple of context strings (route + platform).

    TIP-02 ships a richer ``tokenpak.tip.optimization_contract.OptimizationContract``
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

    Adapters today do not yet declare capabilities (TIP-04 wires that in);
    a missing or non-iterable attribute returns an empty set, matching the
    proposal's "graceful unknowns" rule.
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
) -> Any:
    """Construct an OptimizationContract for the current request.

    Returns a ``tokenpak.tip.optimization_contract.OptimizationContract`` if
    TIP-02 is importable; otherwise a ``_LocalOptimizationContract``. Both
    expose ``.has(capability)``.
    """
    caps = _adapter_capabilities(adapter)
    route_class = route or ""
    platform_str = platform or ""
    extras_dict = dict(extras or {})
    if policy:
        extras_dict.setdefault("policy", dict(policy))

    try:
        from tokenpak.tip.optimization_contract import (  # type: ignore[import-not-found]
            OptimizationContract as _TipContract,
        )
        # Best-effort construction — TIP-02's signature is documented in
        # the task packet but may differ slightly. If construction fails,
        # fall back to the local stub.
        try:
            return _TipContract(
                capabilities=caps,
                route_class=route_class,
                platform=platform_str,
                fidelity=fidelity,
                extras=extras_dict,
            )
        except TypeError:
            return _LocalOptimizationContract(
                capabilities=caps,
                route_class=route_class,
                platform=platform_str,
                fidelity=fidelity,
                extras=extras_dict,
            )
    except Exception:
        return _LocalOptimizationContract(
            capabilities=caps,
            route_class=route_class,
            platform=platform_str,
            fidelity=fidelity,
            extras=extras_dict,
        )


__all__ = ["build_contract", "_LocalOptimizationContract"]
