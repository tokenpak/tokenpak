# SPDX-License-Identifier: Apache-2.0
"""TIP optimization fidelity policy — content preservation requirements.

``FidelityPolicy`` declares how aggressively the optimization layer may
alter request or context content for a given stage/route combination.

This is distinct from ``tokenpak.agent.compression.fidelity_tiers.FidelityTier``
(L0_RAW → L4_SUMMARY), which is a *compression level ladder* used by the
agent-side context manager. ``FidelityPolicy`` is a *proxy-side safety gate*
that constrains what the optimization pipeline may do to request bytes.
"""

from __future__ import annotations

from enum import Enum


class FidelityPolicy(str, Enum):
    """Content preservation requirements for optimization stages.

    Inherits from ``str`` for JSON/YAML compatibility.

    Enforcement contract (proxy/optimization/):
    - Stages MUST check the active ``FidelityPolicy`` before mutating content.
    - A stage that cannot satisfy the required policy MUST skip and emit a
      ``StageTrace`` with ``skip_reason`` explaining the bypass.
    - Policy is determined per-request from the ``OptimizationRouteClass``
      and any adapter-supplied overrides.
    """

    LOSSLESS_REQUIRED = "lossless_required"
    """Exact text preservation required.

    Applies to code blocks, file paths, function/class signatures, exact
    error messages, JSON/YAML schemas, command output, and diff hunks.
    Compression may remove duplicate/irrelevant prose but MUST NOT alter
    protected spans. Response reuse is prohibited.
    """

    SEMANTIC_SAFE = "semantic_safe"
    """Semantic meaning must be preserved; wording may change.

    Allows summarization and rewriting of non-critical context that does
    not contain protected span types. Response reuse requires explicit
    enablement and conservative similarity thresholds.
    """

    AGGRESSIVE_OK = "aggressive_ok"
    """High compression allowed.

    The optimization layer may apply aggressive context reduction. Still
    subject to protected span rules for any embedded code/schema fragments.
    """

    CACHE_RESPONSE_SAFE = "cache_response_safe"
    """Response reuse explicitly allowed for this request class.

    Used for safe low-risk route classes (status_check,
    configuration_inspection) where response identity across similar
    requests is acceptable. Must still respect similarity thresholds.
    """

    NO_OPTIMIZE = "no_optimize"
    """Bypass all optimization stages.

    Used when an adapter or caller explicitly opts out of optimization,
    or when a safety classifier flags the request as high-risk.
    """

    @property
    def allows_response_reuse(self) -> bool:
        return self == FidelityPolicy.CACHE_RESPONSE_SAFE

    @property
    def allows_compression(self) -> bool:
        return self in {
            FidelityPolicy.SEMANTIC_SAFE,
            FidelityPolicy.AGGRESSIVE_OK,
            FidelityPolicy.CACHE_RESPONSE_SAFE,
        }

    @property
    def requires_protected_span_check(self) -> bool:
        return self != FidelityPolicy.NO_OPTIMIZE


# Default policy mapping per OptimizationRouteClass.
# Proxy stages resolve the active policy from this map unless
# an adapter or caller supplies an explicit override.
#
# Import note: delayed import to avoid circular dependency with route_contract.
def default_policy_for_route(route_class: "OptimizationRouteClass") -> FidelityPolicy:
    """Return the default ``FidelityPolicy`` for a given route class."""
    from tokenpak.tip.route_contract import OptimizationRouteClass  # local import

    _MAP: dict["OptimizationRouteClass", FidelityPolicy] = {
        OptimizationRouteClass.GENERAL_CHAT: FidelityPolicy.SEMANTIC_SAFE,
        OptimizationRouteClass.STATUS_CHECK: FidelityPolicy.CACHE_RESPONSE_SAFE,
        OptimizationRouteClass.CONFIGURATION_INSPECTION: FidelityPolicy.CACHE_RESPONSE_SAFE,
        OptimizationRouteClass.CODE_GENERATION: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.CODE_EDIT: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.CODE_REVIEW: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.DEBUGGING: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.TEST_FAILURE: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.LOG_ANALYSIS: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.GIT_DIFF_REVIEW: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.SHELL_COMMAND_ANALYSIS: FidelityPolicy.LOSSLESS_REQUIRED,
        OptimizationRouteClass.DOCUMENTATION_GENERATION: FidelityPolicy.SEMANTIC_SAFE,
        OptimizationRouteClass.SUMMARIZATION: FidelityPolicy.AGGRESSIVE_OK,
        OptimizationRouteClass.RESEARCH: FidelityPolicy.SEMANTIC_SAFE,
        OptimizationRouteClass.PLANNING: FidelityPolicy.SEMANTIC_SAFE,
        OptimizationRouteClass.UNKNOWN: FidelityPolicy.LOSSLESS_REQUIRED,
    }
    return _MAP.get(route_class, FidelityPolicy.LOSSLESS_REQUIRED)


__all__ = ["FidelityPolicy", "default_policy_for_route"]
