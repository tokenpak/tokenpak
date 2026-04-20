"""TokenPak Enterprise — SLA Routing Interface.

Defines the abstract interface for SLA-aware model routing.
Routes requests to models/providers that meet latency, availability,
and reliability targets.

Usage::

    from tokenpak.enterprise.sla import SLARouter, SLAProfile

    router = SLARouter()
    profile = router.get_profile("high-availability")
    best_model = router.resolve("openai/gpt-4o", profile=profile)
    print(f"Routing to: {best_model}")

    status = router.status()
    print(f"Current SLA compliance: {status.compliance_pct:.1f}%")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class SLATier(str, Enum):
    """SLA tier levels."""

    STANDARD = "standard"  # Best-effort (OSS default)
    ENHANCED = "enhanced"  # Pro/Team: retry + fallback
    GUARANTEED = "guaranteed"  # Enterprise: SLA-backed routing


@dataclass
class SLAProfile:
    """SLA contract parameters for a routing tier."""

    name: str
    tier: SLATier
    max_latency_ms: int = 5000  # p95 target
    min_availability_pct: float = 99.0  # monthly uptime target
    max_error_rate_pct: float = 1.0
    fallback_models: list[str] = field(default_factory=list)
    priority_providers: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SLAStatus:
    """Current SLA compliance snapshot."""

    profile: str
    tier: SLATier
    compliance_pct: float  # Rolling 30-day compliance
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    availability_pct: float
    error_rate_pct: float
    period_start: str  # ISO8601
    period_end: str  # ISO8601
    incidents: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """Result of SLA routing resolution."""

    original_model: str
    resolved_model: str
    provider: str
    sla_profile: str
    reason: str
    estimated_latency_ms: Optional[float] = None


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class SLARouterBase(ABC):
    """Abstract SLA router — plug in an Enterprise implementation."""

    @abstractmethod
    def list_profiles(self) -> list[SLAProfile]:
        """Return all configured SLA profiles."""

    @abstractmethod
    def get_profile(self, name: str) -> Optional[SLAProfile]:
        """Return a named SLA profile."""

    @abstractmethod
    def set_profile(self, profile: SLAProfile) -> None:
        """Create or update an SLA profile."""

    @abstractmethod
    def resolve(
        self,
        model: str,
        profile: Optional[SLAProfile] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> RoutingDecision:
        """
        Choose the best model/provider endpoint for the given SLA constraints.

        Parameters
        ----------
        model:
            Requested model (may be overridden by SLA routing).
        profile:
            SLA profile to enforce. Defaults to tenant default profile.
        context:
            Optional metadata: ``user``, ``urgency``, ``data_class``, etc.
        """

    @abstractmethod
    def status(self, profile_name: Optional[str] = None) -> SLAStatus:
        """Return current SLA compliance metrics."""


# ---------------------------------------------------------------------------
# Stub implementation
# ---------------------------------------------------------------------------


class SLARouter(SLARouterBase):
    """
    Stub SLA router.

    Full implementation available with Enterprise license.
    Non-Enterprise tiers use standard pass-through routing.
    """

    _ENTERPRISE_ONLY_MSG = (
        "TOKENPAK  |  Enterprise Feature\n"
        "────────────────────────────────\n\n"
        "SLA Routing requires an Enterprise license.\n"
        "Current tier: {tier}\n\n"
        "Learn more: https://tokenpak.dev/enterprise\n"
    )

    def __init__(self) -> None:
        self._delegate: Optional[SLARouterBase] = self._try_load_enterprise()

    def _try_load_enterprise(self) -> Optional[SLARouterBase]:
        try:
            from tokenpak.agent.license.activation import is_enterprise

            if not is_enterprise():
                return None
        except Exception:
            pass
        return None

    def _tier_name(self) -> str:
        try:
            from tokenpak.agent.license.activation import get_plan

            return get_plan().tier.value.upper()
        except Exception:
            return "OSS"

    def _upgrade_msg(self) -> str:
        return self._ENTERPRISE_ONLY_MSG.format(tier=self._tier_name())

    def list_profiles(self) -> list[SLAProfile]:
        if self._delegate:
            return self._delegate.list_profiles()
        print(self._upgrade_msg())
        return []

    def get_profile(self, name: str) -> Optional[SLAProfile]:
        if self._delegate:
            return self._delegate.get_profile(name)
        print(self._upgrade_msg())
        return None

    def set_profile(self, profile: SLAProfile) -> None:
        if self._delegate:
            self._delegate.set_profile(profile)
            return
        print(self._upgrade_msg())

    def resolve(
        self,
        model: str,
        profile: Optional[SLAProfile] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> RoutingDecision:
        if self._delegate:
            return self._delegate.resolve(model, profile, context)
        # Non-Enterprise: pass through with no SLA routing
        provider = model.split("/")[0] if "/" in model else "unknown"
        return RoutingDecision(
            original_model=model,
            resolved_model=model,
            provider=provider,
            sla_profile="standard",
            reason="SLA routing not active (non-Enterprise tier)",
        )

    def status(self, profile_name: Optional[str] = None) -> SLAStatus:
        if self._delegate:
            return self._delegate.status(profile_name)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        print(self._upgrade_msg())
        return SLAStatus(
            profile=profile_name or "standard",
            tier=SLATier.STANDARD,
            compliance_pct=0.0,
            p50_latency_ms=0.0,
            p95_latency_ms=0.0,
            p99_latency_ms=0.0,
            availability_pct=0.0,
            error_rate_pct=0.0,
            period_start=now,
            period_end=now,
        )
