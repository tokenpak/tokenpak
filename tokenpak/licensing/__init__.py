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
    """Resolve the license file path through the canonical path resolver.

    Resolution order:
      1. ``TOKENPAK_LICENSE_FILE`` env var (explicit override).
      2. ``<TOKENPAK_HOME>/license.json`` via ``tokenpak._paths.under``,
         which honors ``TOKENPAK_HOME`` then canonical ``~/.tpk/`` then
         legacy ``~/.tokenpak/``.

    Beta-1 regression fix (found during validation): previously this hardcoded
    ``Path.home() / ".tokenpak" / "license.json"``, which silently
    bypassed ``TOKENPAK_HOME``. On a host with the env set elsewhere
    that meant ``activate`` would clobber the *real* home's
    ``~/.tokenpak/license.json`` instead of writing under the test
    sandbox — a sandbox-escape + home-directory boundary violation in one.
    """
    override = os.environ.get("TOKENPAK_LICENSE_FILE")
    if override:
        return Path(override)
    from tokenpak import _paths

    return _paths.under("license.json")


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


# Explicit, unmistakable prefix for the dev-shim activation path. Chosen so a
# real purchased key can never accidentally trip the shim, and so the bypass
# is greppable in any stored license.json (mirrors the manual temp-gate's
# ``LOCAL-DEV-TEMP-NOT-A-REAL-KEY`` marker).
_DEVSHIM_PREFIX = "TPK-DEVSHIM-"


def _devshim_tier(key: str) -> str:
    """Parse the requested tier out of a dev-shim key.

    Shape: ``TPK-DEVSHIM-<TIER>-<anything>``. Recognizes any *paid* tier
    name from the canonical tier constants (no separate hardcoded list,
    per ``feedback_always_dynamic``); defaults to Pro when no tier segment
    is present.
    """
    paid = {TIER_PRO, TIER_TEAM, TIER_ENTERPRISE}
    for segment in key.lower().split("-"):
        if segment in paid:
            return segment
    return TIER_PRO


def activate(key: str, *, email: str = "") -> ActivationResult:
    """Store a license key for future validation.

    No entitlement server exists yet, so we cannot know what tier a key
    buys. Tier defaults to Free; status is ``pending_validation`` until
    a real validator lands. This intentionally fails *safe* — anybody
    who tries to bypass payment by inventing a string sees Free-tier
    behavior, never Pro.

    The shape check below rejects obviously invalid inputs (empty,
    whitespace, too short, non-printable, internal placeholder strings)
    so the CLI never claims success on garbage. Genuine keys passing
    the shape check are stored verbatim for the validator to verify
    once wired (Pro daemon coordination).

    Important: this function does NOT grant Pro entitlements on its
    own. It is a *store-and-stage* step. ``is_feature_enabled`` is the
    single choke point that decides what a key buys.

    Two paths *can* produce an active paid tier:

    1. **Daemon-verified (production):** ``_consult_daemon_for_tier``
       below asks the local Pro daemon's ``/v1/features`` endpoint and
       upgrades the stored license only on a verified, non-placeholder
       signature. This is the real path; it is fail-closed.
    2. **Dev shim (local development only):** when the environment sets
       ``TOKENPAK_LICENSE_DEV_SHIM=1`` *and* the key starts with
       ``TPK-DEVSHIM-``, activation writes ``status="active"`` at the
       requested tier locally, bypassing the daemon. This is OFF by
       default and exists purely so the activation path is reproducible
       in tests/dev without K3 signing-key material. OSS users never see
       it unless they explicitly opt in.
    """
    import re

    key = (key or "").strip()
    if not key:
        return ActivationResult(
            ok=False, summary="No license key provided.",
            error="empty_key",
        )
    if len(key) < 16:
        return ActivationResult(
            ok=False,
            summary=(
                "License key is implausibly short (need ≥ 16 characters). "
                "Paste the full key from your purchase confirmation."
            ),
            error="key_too_short",
        )
    if not key.isprintable():
        return ActivationResult(
            ok=False,
            summary="License key contains non-printable characters.",
            error="non_printable_key",
        )
    # Allow alphanumeric, dash, dot, underscore, slash, plus, equals
    # (covers base64url + dotted token formats).
    if not re.match(r"^[A-Za-z0-9._/+=\-]+$", key):
        return ActivationResult(
            ok=False,
            summary=(
                "License key has unexpected characters. Keys are "
                "alphanumeric plus '-._/+='."
            ),
            error="bad_key_charset",
        )
    if key.lower() in {"test", "demo", "placeholder", "tbd", "free"}:
        return ActivationResult(
            ok=False,
            summary=f"{key!r} is not a real license key.",
            error="placeholder_key",
        )
    import datetime

    # ── Dev-shim activation (offline Pro local-daemon acceptance path) ───
    # When TOKENPAK_LICENSE_DEV_SHIM=1 AND the key carries the explicit
    # TPK-DEVSHIM- prefix, activate the requested tier locally WITHOUT a
    # daemon round-trip. This makes the Pro activation path reproducible
    # for testing without provisioning signing-key material or standing up
    # a Pro daemon, and lets us retire the hand-edited ~/.tokenpak/license.json
    # temp gate.
    #
    # PUBLIC SAFETY: OFF by default. With the env var unset (the only state
    # OSS users ever see) this block is skipped entirely and activation falls
    # through to the fail-closed daemon-consultation path below. A hand-edited
    # license.json still never unlocks Pro — is_feature_enabled remains the
    # choke point; only this explicitly-opted-in dev path or a daemon-verified
    # signature can set status="active" at a paid tier.
    if (
        os.environ.get("TOKENPAK_LICENSE_DEV_SHIM") == "1"
        and key.upper().startswith(_DEVSHIM_PREFIX)
    ):
        shim_tier = _devshim_tier(key)
        lic = License(
            tier=shim_tier,
            key=key,
            activated_at=datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            email=email,
            status="active",
        )
        try:
            save_license(lic)
        except Exception as exc:
            return ActivationResult(
                ok=False, summary="Could not write license file.", error=str(exc),
            )
        return ActivationResult(
            ok=True,
            license=lic,
            summary=(
                f"License activated via DEV SHIM (TOKENPAK_LICENSE_DEV_SHIM=1) "
                f"at tier={lic.tier}. This is a local development bypass, NOT a "
                f"real entitlement — unset the env var and run 'tokenpak "
                f"deactivate' to revert to Free."
            ),
        )

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
    # Best-effort: ask the Pro daemon for the verified tier. Fails closed
    # — daemon unreachable, unverified signature, placeholder verifier
    # key, or timeout keeps the stored license at tier=FREE /
    # status=pending_validation. Local file edits MUST NEVER unlock Pro.
    daemon_state, advisory = _consult_daemon_for_tier(lic)
    if daemon_state == "verified":
        summary = (
            f"License key stored and verified by the Pro daemon. "
            f"Active tier: {lic.tier}."
        )
    elif daemon_state == "unverified":
        summary = (
            "License key stored. Pro daemon rejected verification "
            f"({advisory}). Pro features remain locked."
        )
    elif daemon_state == "placeholder":
        summary = (
            "License key stored. Pro daemon is shipping a placeholder "
            "license public key (production rotation pending). "
            "Verification is advisory only — Pro features remain locked."
        )
    elif daemon_state == "unreachable":
        summary = (
            "License key stored. Pro daemon not running — Pro features "
            "remain locked until the daemon is reachable and "
            "verification succeeds."
        )
    else:
        summary = (
            "License key stored (pending validation — Pro tier not live "
            "yet). Free-tier features remain active."
        )
    return ActivationResult(ok=True, summary=summary, license=lic)


def _consult_daemon_for_tier(lic: "License") -> tuple[str, str]:
    """Ask the Pro daemon's ``/v1/features`` endpoint about ``lic``.

    Returns ``(state, advisory)`` where ``state`` is one of:

      ``"verified"``    — daemon returned ``signature.verified=true``
                          AND ``signature.key_is_placeholder=false``
                          AND ``is_valid=true``. ``lic`` was updated
                          in place and saved with the daemon-reported
                          tier + status="active".
      ``"unverified"``  — daemon returned ``signature.verified=false``;
                          advisory carries the failure reason.
      ``"placeholder"`` — daemon reports placeholder verifier key.
      ``"unreachable"`` — daemon probe failed, sock-info missing, HTTP
                          error, malformed payload, or any exception.
      ``"pending"``     — kept for forward-compatibility.

    Fail-closed: any exception or unexpected payload shape collapses
    to ``"unreachable"``. OSS NEVER claims Pro on partial info. Local
    ``license.json`` edits never bypass this — the function only
    writes back when the daemon explicitly returns a verified envelope.
    """
    import json
    import urllib.error
    import urllib.request

    try:
        from tokenpak.licensing.daemon_probe import (
            detect_daemon_state,
            sock_info_path,
        )
    except ImportError:
        return ("unreachable", "daemon_probe_unavailable")

    if detect_daemon_state() != "active":
        return ("unreachable", "daemon_not_listening")

    try:
        info_path = sock_info_path()
        info = json.loads(info_path.read_text(encoding="utf-8"))
        port = info.get("port")
        if not isinstance(port, int):
            return ("unreachable", "sock_info_missing_port")
    except (OSError, json.JSONDecodeError, ValueError):
        return ("unreachable", "sock_info_unreadable")

    url = f"http://127.0.0.1:{port}/v1/features"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return ("unreachable", f"http_error:{type(exc).__name__}")

    if not isinstance(payload, dict):
        return ("unreachable", "malformed_payload")

    sig = payload.get("signature") or {}
    if not isinstance(sig, dict):
        return ("unreachable", "malformed_signature_envelope")

    if sig.get("key_is_placeholder", False):
        return ("placeholder", "verifier_key_is_placeholder")

    if not sig.get("verified", False):
        reason = sig.get("reason", "unknown")
        return ("unverified", f"signature_{reason}")

    if not payload.get("is_valid", False):
        return ("unverified", str(payload.get("degraded_reason", "not_valid")))

    daemon_tier = str(payload.get("tier", TIER_FREE)).lower()
    if daemon_tier in (TIER_PRO, TIER_TEAM, TIER_ENTERPRISE):
        lic.tier = daemon_tier
        lic.status = "active"
        try:
            save_license(lic)
        except Exception:
            return ("unreachable", "save_failed")
        return ("verified", f"daemon_tier:{daemon_tier}")

    return ("unverified", f"daemon_tier_not_paid:{daemon_tier}")


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


def discover_plans() -> list[dict[str, Any]]:
    """Derive the plan catalog dynamically from ``_GATES`` (Beta 1).

    Tier presence is data-driven: a tier appears in the catalog only if
    at least one feature is gated to it, which means adding a new
    feature with a new tier automatically shows up here — no hardcoded
    list to maintain (``feedback_always_dynamic.md``).

    Pricing data is read from a discovery file at
    ``<TOKENPAK_HOME>/pricing.json`` when present; otherwise the
    ``price`` field is the string ``"unannounced"`` (honest, not the
    misleading ``"TBD"`` that the Beta-1 readiness audit flagged).

    Returns a list of dicts:
        {tier, label, feature_count, features, price, blurb}
    in canonical order Free → Pro → Team → Enterprise.
    """
    # Reverse the gate table: tier → [features]
    tier_features: dict[str, list[str]] = {TIER_FREE: []}
    for feature, required in _GATES.items():
        tier_features.setdefault(required, []).append(feature)

    pricing = _load_pricing_manifest()
    blurbs = _default_blurbs()
    order = (TIER_FREE, TIER_PRO, TIER_TEAM, TIER_ENTERPRISE)

    catalog: list[dict[str, Any]] = []
    for tier in order:
        if tier != TIER_FREE and tier not in tier_features:
            continue
        feats = sorted(tier_features.get(tier, []))
        catalog.append({
            "tier": tier,
            "label": describe_tier(tier),
            "feature_count": len(feats),
            "features": feats,
            "price": pricing.get(tier, "unannounced"),
            "blurb": blurbs.get(tier, ""),
        })
    return catalog


def _load_pricing_manifest() -> dict[str, str]:
    """Read ``<TOKENPAK_HOME>/pricing.json`` if present.

    Shape: ``{"free": "$0", "pro": "$X/mo", ...}``. Missing / unparseable
    file → empty dict (callers fall back to ``"unannounced"``).
    """
    try:
        from tokenpak import _paths

        p = _paths.under("pricing.json")
        if not p.exists():
            return {}
        import json as _json

        data = _json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def _default_blurbs() -> dict[str, str]:
    return {
        TIER_FREE: (
            "Full Free-tier feature set — proxy, vault, basic "
            "compression, dashboard, savings tracking."
        ),
        TIER_PRO: (
            "Adds advanced compression, smart routing, session "
            "telemetry, trace + replay, CSV/JSON export."
        ),
        TIER_TEAM: (
            "Adds budget enforcement, OAuth, real-time stats API, "
            "shared vault, handoff system, workflow budgets."
        ),
        TIER_ENTERPRISE: (
            "Adds A/B testing, shadow mode, regression detection, "
            "FinOps + audit pages, DLP/PII scanning, connection pooling."
        ),
    }


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
