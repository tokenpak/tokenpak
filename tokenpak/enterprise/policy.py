"""TokenPak Enterprise — Policy Engine Interface.

Defines the abstract interface for the enterprise policy engine.
Actual enforcement logic is injected via the plugin system.

Usage::

    from tokenpak.enterprise.policy import PolicyEngine, Policy, PolicyAction

    engine = PolicyEngine()
    policies = engine.list_policies()
    for p in policies:
        print(f"{p.name}: {p.action.value}")

    result = engine.enforce("openai/gpt-4o", context={"user": "alice"})
    if not result.allowed:
        raise PermissionError(result.reason)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class PolicyAction(str, Enum):
    """What happens when a policy rule matches."""

    ALLOW = "allow"
    DENY = "deny"
    WARN = "warn"
    AUDIT = "audit"
    REROUTE = "reroute"


class PolicyScope(str, Enum):
    """What a policy applies to."""

    MODEL = "model"
    PROVIDER = "provider"
    USER = "user"
    TEAM = "team"
    GLOBAL = "global"


@dataclass
class Policy:
    """A single policy rule."""

    id: str
    name: str
    description: str
    scope: PolicyScope
    action: PolicyAction
    conditions: dict[str, Any] = field(default_factory=dict)
    priority: int = 100  # Lower = higher priority
    enabled: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class EnforcementResult:
    """Result of a policy enforcement check."""

    allowed: bool
    matched_policy: Optional[Policy] = None
    reason: str = ""
    action: PolicyAction = PolicyAction.ALLOW
    reroute_to: Optional[str] = None  # Model to reroute to, if action=REROUTE
    audit_required: bool = False


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class PolicyEngineBase(ABC):
    """Abstract policy engine — plug in an Enterprise implementation."""

    @abstractmethod
    def list_policies(self) -> list[Policy]:
        """Return all configured policies."""

    @abstractmethod
    def get_policy(self, policy_id: str) -> Optional[Policy]:
        """Return a specific policy by ID."""

    @abstractmethod
    def set_policy(self, policy: Policy) -> None:
        """Create or update a policy."""

    @abstractmethod
    def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy. Returns True if deleted."""

    @abstractmethod
    def enforce(
        self,
        model: str,
        context: Optional[dict[str, Any]] = None,
    ) -> EnforcementResult:
        """
        Evaluate all applicable policies for a given request context.

        Parameters
        ----------
        model:
            The model identifier being requested (e.g. ``openai/gpt-4o``).
        context:
            Optional dict with keys like ``user``, ``team``, ``data_class``,
            ``tokens``, etc. used for condition matching.

        Returns
        -------
        EnforcementResult
            Whether the request is allowed and why.
        """


# ---------------------------------------------------------------------------
# Stub implementation (Enterprise license required for full engine)
# ---------------------------------------------------------------------------


class PolicyEngine(PolicyEngineBase):
    """
    Stub policy engine.

    The real implementation is loaded from the Enterprise plugin when a valid
    Enterprise license is present. This stub preserves the interface and
    returns sensible defaults for non-Enterprise tiers.
    """

    _ENTERPRISE_ONLY_MSG = (
        "TOKENPAK  |  Enterprise Feature\n"
        "────────────────────────────────\n\n"
        "Policy Engine requires an Enterprise license.\n"
        "Current tier: {tier}\n\n"
        "Learn more: https://tokenpak.dev/enterprise\n"
    )

    def __init__(self) -> None:
        self._delegate: Optional[PolicyEngineBase] = self._try_load_enterprise()

    def _try_load_enterprise(self) -> Optional[PolicyEngineBase]:
        """Attempt to load the real Enterprise policy engine."""
        try:
            from tokenpak.agent.license.activation import is_enterprise

            if not is_enterprise():
                return None
            # Future: import and return the real engine
            # from tokenpak.enterprise._impl.policy import RealPolicyEngine
            # return RealPolicyEngine()
        except Exception:
            pass
        return None

    def _tier_name(self) -> str:
        try:
            from tokenpak.agent.license.activation import get_plan

            result = get_plan()
            return result.tier.value.upper()
        except Exception:
            return "OSS"

    def _upgrade_msg(self) -> str:
        return self._ENTERPRISE_ONLY_MSG.format(tier=self._tier_name())

    def list_policies(self) -> list[Policy]:
        if self._delegate:
            return self._delegate.list_policies()
        print(self._upgrade_msg())
        return []

    def get_policy(self, policy_id: str) -> Optional[Policy]:
        if self._delegate:
            return self._delegate.get_policy(policy_id)
        print(self._upgrade_msg())
        return None

    def set_policy(self, policy: Policy) -> None:
        if self._delegate:
            self._delegate.set_policy(policy)
            return
        print(self._upgrade_msg())

    def delete_policy(self, policy_id: str) -> bool:
        if self._delegate:
            return self._delegate.delete_policy(policy_id)
        print(self._upgrade_msg())
        return False

    def enforce(
        self,
        model: str,
        context: Optional[dict[str, Any]] = None,
    ) -> EnforcementResult:
        if self._delegate:
            return self._delegate.enforce(model, context)
        # Non-Enterprise: allow everything (no policy engine active)
        return EnforcementResult(allowed=True, reason="No policy engine (non-Enterprise tier)")
