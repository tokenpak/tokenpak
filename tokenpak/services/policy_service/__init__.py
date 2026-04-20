"""Policy gates: budget, cost, rate-limit, content-policy.

Pipeline-side gates that consult ``security/`` (DLP, permissions) and
budget/cost state before dispatch. Policy decisions are recorded via
``telemetry_service`` so the dashboard and alerts can surface them.

Phase 2 scaffold. Scope negotiated per Q7 2026-04-20: policy_service
drives enforcement; security/ owns the primitives. See also the §3.9
audit rubric section for compatibility with the plane rules.
"""

from __future__ import annotations
