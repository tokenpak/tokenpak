# SPDX-License-Identifier: Apache-2.0
"""License management — Free-tier defaults today, Pro/Team/Enterprise ready.

This module implements I7 (License Activation), I8 (License Validation), and
I9 (License Store) from the Free-tier feature list. No real entitlement
server exists yet, so Free is the only live tier — but the API surface is
shaped so a Pro backend can plug in later without CLI changes.

Design invariants:
    - Missing key / missing license.json → Free tier (never error on clean install)
    - Stored license.json with no "tier" field → Free tier
    - is_feature_enabled(name) is the single choke point for Pro gating
    - License store is JSON at ~/.tokenpak/license.json (human-inspectable)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

TIER_FREE = "free"
TIER_PRO = "pro"
TIER_TEAM = "team"
TIER_ENTERPRISE = "enterprise"

# Feature → minimum tier required. Mirrors Kevin's Free/Pro/Team/Enterprise
# split in the canonical feature list. Free features are implicit (not here).
# Add entries only for gated features; anything absent is treated as Free.
_GATES: dict[str, str] = {
    # Compression (Pro)
    "C3_code_compression": TIER_PRO,
    "C5_log_compression": TIER_PRO,
    "C6_json_yaml_compression": TIER_PRO,
    "C12_query_rewriting": TIER_PRO,
    # Proxy (Pro/Team/Enterprise)
    "R4_smart_routing": TIER_PRO,
    "R8_intent_policy_routing": TIER_PRO,
    "R13_oauth_auth_handling": TIER_TEAM,
    "R14_capsule_integration": TIER_PRO,
    "R15_failover_engine": TIER_PRO,
    "R7_connection_pooling": TIER_ENTERPRISE,
    # Cost / telemetry
    "T3_budget_enforcement": TIER_TEAM,
    "T4_session_telemetry": TIER_PRO,
    "T5_cost_report_generation": TIER_PRO,
    "T9_replay_system": TIER_PRO,
    # Dashboard
    "D2_finops_page": TIER_ENTERPRISE,
    "D3_engineering_page": TIER_PRO,
    "D4_audit_page": TIER_ENTERPRISE,
    "D5_csv_export": TIER_PRO,
    "D6_json_export": TIER_PRO,
    "D7_session_filtering": TIER_PRO,
    "D8_realtime_stats_api": TIER_TEAM,
    # Agentic
    "A1_workflow_engine": TIER_PRO,
    "A2_capabilities_registry": TIER_PRO,
    "A4_failure_memory": TIER_PRO,
    "A6_prefetcher": TIER_PRO,
    "A7_handoff_system": TIER_TEAM,
    "A8_memory_promoter": TIER_PRO,
    "A9_precondition_gates": TIER_PRO,
    "A10_state_collector": TIER_PRO,
    "A11_workflow_budget": TIER_TEAM,
    "A12_workflow_performance": TIER_PRO,
    "A13_validation_framework": TIER_PRO,
    "A14_runbook_generator": TIER_ENTERPRISE,
    # CLI (Pro/Team/Enterprise commands)
    "L8_trace": TIER_PRO,
    "L9_replay": TIER_PRO,
    "L10_route_manage": TIER_PRO,
    "L11_route_test": TIER_PRO,
    "L16_budget_manage": TIER_TEAM,
    "L19_metrics_export": TIER_PRO,
    "L20_policy": TIER_ENTERPRISE,
    "L24_trigger": TIER_PRO,
    "L25_workflow": TIER_PRO,
    # Advanced
    "X1_ab_testing": TIER_ENTERPRISE,
    "X2_shadow_mode": TIER_ENTERPRISE,
    "X3_regression_detection": TIER_ENTERPRISE,
    "X4_baseline_registry": TIER_ENTERPRISE,
    "X5_artifact_reuse": TIER_PRO,
    "X6_team_shared_vault": TIER_TEAM,
    "X7_agent_registry": TIER_TEAM,
    "X8_teacher_framework": TIER_ENTERPRISE,
    # Infra
    "I4_security_pii_dlp": TIER_ENTERPRISE,
    "I10_oauth_manager": TIER_TEAM,
}

_TIER_ORDER = {
    TIER_FREE: 0,
    TIER_PRO: 1,
    TIER_TEAM: 2,
    TIER_ENTERPRISE: 3,
}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _license_path() -> Path:
    override = os.environ.get("TOKENPAK_LICENSE_FILE")
    if override:
        return Path(override)
    return Path.home() / ".tokenpak" / "license.json"


@dataclass
class License:
    """Current active license — Free by default, unless a key is stored."""

    tier: str = TIER_FREE
    key: str = ""
    activated_at: Optional[str] = None
    expires_at: Optional[str] = None      # ISO date; None = no expiry
    seats: int = 1
    email: str = ""
    status: str = "active"                # active | pending_validation | expired | revoked
    features_override: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "key": self.key,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "seats": self.seats,
            "email": self.email,
            "status": self.status,
            "features_override": list(self.features_override),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "License":
        return cls(
            tier=str(data.get("tier") or TIER_FREE).lower(),
            key=str(data.get("key") or ""),
            activated_at=data.get("activated_at"),
            expires_at=data.get("expires_at"),
            seats=int(data.get("seats") or 1),
            email=str(data.get("email") or ""),
            status=str(data.get("status") or "active").lower(),
            features_override=list(data.get("features_override") or []),
        )


def load_license() -> License:
    """Read the stored license, or return Free defaults if absent/malformed."""
    p = _license_path()
    if not p.exists():
        return License()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return License()
    if not isinstance(data, dict):
        return License()
    return License.from_dict(data)


def save_license(lic: License) -> None:
    """Persist license to disk (atomic write)."""
    p = _license_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(lic.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, p)


def delete_license() -> bool:
    """Remove stored license (revert to Free). Returns True if a file was removed."""
    p = _license_path()
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------------------
# Activation (placeholder — no live entitlement server yet)
# ---------------------------------------------------------------------------


@dataclass
class ActivationResult:
    ok: bool
    summary: str
    license: Optional[License] = None
    error: Optional[str] = None


def activate(key: str, *, email: str = "") -> ActivationResult:
    """Store a license key for future validation.

    Since no entitlement server exists yet, we don't know what tier a key
    buys. Store it with status='pending_validation' and default to Free
    entitlements until a real validator lands. This lets early buyers
    install their key without the CLI failing, and any gated feature still
    correctly reports Free until validation completes.
    """
    key = key.strip()
    if not key:
        return ActivationResult(
            ok=False, summary="No license key provided.",
            error="empty_key",
        )
    import datetime
    lic = License(
        tier=TIER_FREE,                    # validator upgrades this once wired
        key=key,
        activated_at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        email=email,
        status="pending_validation",
    )
    try:
        save_license(lic)
    except Exception as exc:
        return ActivationResult(
            ok=False, summary="Could not write license file.", error=str(exc),
        )
    return ActivationResult(
        ok=True,
        summary=(
            "License key stored (pending validation — Pro tier not live yet). "
            "Free-tier features remain active."
        ),
        license=lic,
    )


def deactivate() -> bool:
    """Remove stored license — reverts to Free."""
    return delete_license()


# ---------------------------------------------------------------------------
# Feature gating
# ---------------------------------------------------------------------------


def is_feature_enabled(feature: str, *, lic: Optional[License] = None) -> bool:
    """Return True if `feature` is available under the current/given license.

    Features absent from _GATES are Free — always enabled. This is the
    single choke point any callsite should route through when guarding
    Pro/Team/Enterprise functionality.
    """
    if feature not in _GATES:
        return True
    required = _GATES[feature]
    if lic is None:
        lic = load_license()
    # Explicit per-license feature override takes precedence
    if feature in lic.features_override:
        return True
    if lic.status != "active":
        # pending_validation / expired / revoked → Free-only
        return required == TIER_FREE
    return _TIER_ORDER.get(lic.tier, 0) >= _TIER_ORDER[required]


def describe_tier(tier: str) -> str:
    """Human-readable tier label."""
    return {
        TIER_FREE: "Free",
        TIER_PRO: "Pro",
        TIER_TEAM: "Team",
        TIER_ENTERPRISE: "Enterprise",
    }.get(tier, tier.title())


def summary_for_cli(lic: Optional[License] = None) -> dict[str, Any]:
    """Everything the CLI commands need to render in one dict."""
    if lic is None:
        lic = load_license()
    features = {}
    for feat, tier in _GATES.items():
        features[feat] = {
            "min_tier": tier,
            "enabled": is_feature_enabled(feat, lic=lic),
        }
    return {
        "tier": lic.tier,
        "tier_label": describe_tier(lic.tier),
        "status": lic.status,
        "email": lic.email,
        "activated_at": lic.activated_at,
        "expires_at": lic.expires_at,
        "seats": lic.seats,
        "has_key": bool(lic.key),
        "license_path": str(_license_path()),
        "gated_feature_count": len(_GATES),
        "enabled_gated_count": sum(1 for f in features.values() if f["enabled"]),
    }


__all__ = [
    "TIER_FREE", "TIER_PRO", "TIER_TEAM", "TIER_ENTERPRISE",
    "License", "ActivationResult",
    "load_license", "save_license", "delete_license",
    "activate", "deactivate",
    "is_feature_enabled", "describe_tier", "summary_for_cli",
]
