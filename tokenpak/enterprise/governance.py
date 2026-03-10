"""TokenPak Enterprise — Governance Rules Interface.

Defines the abstract interface for the governance rules engine.
Governance covers data classification, retention, access controls,
and cross-policy rule arbitration.

Usage::

    from tokenpak.enterprise.governance import GovernanceEngine, DataClass

    gov = GovernanceEngine()
    rule = gov.get_rule("no-pii-external")
    gov.classify("This email contains SSN 123-45-6789")  # => DataClass.RESTRICTED
    report = gov.audit_report(period="2026-Q1")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------


class DataClass(str, Enum):
    """Data sensitivity classification levels."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"   # PII, secrets, regulated data


class RuleEffect(str, Enum):
    """Effect of a governance rule when triggered."""

    BLOCK = "block"
    REDACT = "redact"
    WARN = "warn"
    LOG = "log"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class GovernanceRule:
    """A single governance rule."""

    id: str
    name: str
    description: str
    effect: RuleEffect
    data_classes: list[DataClass] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=list)  # model/provider patterns
    conditions: dict[str, Any] = field(default_factory=dict)
    retention_days: Optional[int] = None
    enabled: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class ClassificationResult:
    """Result of data classification."""

    data_class: DataClass
    confidence: float          # 0.0 – 1.0
    detected_patterns: list[str] = field(default_factory=list)
    redacted_text: Optional[str] = None


@dataclass
class GovernanceAuditReport:
    """Governance audit summary for a period."""

    period: str
    generated_at: str
    total_requests: int
    blocked: int
    redacted: int
    warned: int
    data_class_breakdown: dict[str, int] = field(default_factory=dict)
    rule_hits: dict[str, int] = field(default_factory=dict)
    incidents: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class GovernanceEngineBase(ABC):
    """Abstract governance engine — plug in an Enterprise implementation."""

    @abstractmethod
    def list_rules(self) -> list[GovernanceRule]:
        """Return all configured governance rules."""

    @abstractmethod
    def get_rule(self, rule_id: str) -> Optional[GovernanceRule]:
        """Return a specific rule by ID."""

    @abstractmethod
    def set_rule(self, rule: GovernanceRule) -> None:
        """Create or update a rule."""

    @abstractmethod
    def classify(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> ClassificationResult:
        """
        Classify the sensitivity of a text payload.

        Parameters
        ----------
        text:
            The content to classify (prompt, response, or document).
        context:
            Optional metadata: ``user``, ``model``, ``source``, etc.
        """

    @abstractmethod
    def audit_report(
        self,
        period: str,
        rule_ids: Optional[list[str]] = None,
    ) -> GovernanceAuditReport:
        """
        Generate a governance audit report.

        Parameters
        ----------
        period:
            Time period string, e.g. ``"2026-Q1"``, ``"2026-03"``, or an
            ISO date range ``"2026-01-01/2026-03-31"``.
        rule_ids:
            Optional filter — only include these rule IDs. All rules if None.
        """


# ---------------------------------------------------------------------------
# Stub implementation
# ---------------------------------------------------------------------------


class GovernanceEngine(GovernanceEngineBase):
    """
    Stub governance engine.

    Full implementation (PII detection, redaction, retention enforcement)
    available with Enterprise license.
    """

    _ENTERPRISE_ONLY_MSG = (
        "TOKENPAK  |  Enterprise Feature\n"
        "────────────────────────────────\n\n"
        "Governance Engine requires an Enterprise license.\n"
        "Current tier: {tier}\n\n"
        "Learn more: https://tokenpak.dev/enterprise\n"
    )

    def __init__(self) -> None:
        self._delegate: Optional[GovernanceEngineBase] = self._try_load_enterprise()

    def _try_load_enterprise(self) -> Optional[GovernanceEngineBase]:
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

    def list_rules(self) -> list[GovernanceRule]:
        if self._delegate:
            return self._delegate.list_rules()
        print(self._upgrade_msg())
        return []

    def get_rule(self, rule_id: str) -> Optional[GovernanceRule]:
        if self._delegate:
            return self._delegate.get_rule(rule_id)
        print(self._upgrade_msg())
        return None

    def set_rule(self, rule: GovernanceRule) -> None:
        if self._delegate:
            self._delegate.set_rule(rule)
            return
        print(self._upgrade_msg())

    def classify(
        self,
        text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> ClassificationResult:
        if self._delegate:
            return self._delegate.classify(text, context)
        print(self._upgrade_msg())
        return ClassificationResult(
            data_class=DataClass.INTERNAL,
            confidence=0.0,
            detected_patterns=[],
        )

    def audit_report(
        self,
        period: str,
        rule_ids: Optional[list[str]] = None,
    ) -> GovernanceAuditReport:
        if self._delegate:
            return self._delegate.audit_report(period, rule_ids)
        from datetime import datetime, timezone

        print(self._upgrade_msg())
        return GovernanceAuditReport(
            period=period,
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_requests=0,
            blocked=0,
            redacted=0,
            warned=0,
        )
