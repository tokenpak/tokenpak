# SPDX-License-Identifier: Apache-2.0
"""Current ``tokenpak.licensing`` surface — activation, gating, dev-shim.

This file replaces three orphaned test modules that exercised a license
subsystem removed from OSS long ago (``tokenpak.license.*``,
``tokenpak._internal.license.*``, ``tokenpak.infrastructure.license_*`` —
the RSA key-crypto + ``LicenseValidator`` generation). Those modules no
longer exist in the OSS package (the signing crypto moved to the private
``tokenpak-paid`` daemon per Std 25), so their tests skipped permanently
via ``pytest.importorskip`` on non-existent modules and provided zero
coverage. The live surface is the single ``tokenpak.licensing`` module,
covered here.

Key invariants under test:
  - Missing license file → Free tier (never errors on a clean install).
  - ``is_feature_enabled`` is the single Pro gate.
  - Public-default ``activate`` fails closed: an invented key never unlocks
    Pro; status stays ``pending_validation`` unless a Pro daemon verifies it.
  - The dev shim (``TOKENPAK_LICENSE_DEV_SHIM=1`` + ``TPK-DEVSHIM-`` prefix)
    is two-factor and OFF by default.
"""
from __future__ import annotations

import pytest

from tokenpak import licensing
from tokenpak.licensing import (
    TIER_ENTERPRISE,
    TIER_FREE,
    TIER_PRO,
    TIER_TEAM,
    activate,
    daemon_probe,  # noqa: F401  (submodule import for monkeypatch)
    deactivate,
    is_feature_enabled,
    load_license,
)

# A gated Pro feature drawn dynamically from the live gate table, so this
# test never hardcodes a feature name that might be re-tiered later.
_PRO_FEATURE = next(f for f, t in licensing._GATES.items() if t == TIER_PRO)


@pytest.fixture(autouse=True)
def _sandbox_license(tmp_path, monkeypatch):
    """Route the license store to a throwaway file + force the Pro daemon
    'unreachable' so tests are hermetic regardless of host daemon state."""
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(tmp_path / "license.json"))
    monkeypatch.delenv("TOKENPAK_LICENSE_DEV_SHIM", raising=False)
    monkeypatch.setattr(
        licensing.daemon_probe, "detect_daemon_state", lambda **_: "unavailable"
    )
    yield


# ── Defaults ────────────────────────────────────────────────────────────

def test_missing_license_defaults_to_free():
    lic = load_license()
    assert lic.tier == TIER_FREE
    assert lic.status == "active"


def test_free_blocks_pro_feature():
    assert is_feature_enabled(_PRO_FEATURE) is False


def test_unknown_feature_is_free_and_allowed():
    assert is_feature_enabled("Z9_not_a_real_feature") is True


# ── Shape validation (fail-fast on garbage) ──────────────────────────────

@pytest.mark.parametrize(
    "bad",
    ["", "   ", "short", "free", "placeholder", "tbd", "bad key with spaces!!"],
)
def test_activate_rejects_garbage(bad):
    r = activate(bad)
    assert r.ok is False


# ── Public default: fail closed ──────────────────────────────────────────

def test_activate_public_default_does_not_unlock_pro():
    """No shim + no daemon: a plausible key is stored but stays pending."""
    r = activate("PLAUSIBLE-LOOKING-KEY-1234567890")
    assert r.ok is True
    assert r.license.tier == TIER_FREE
    assert r.license.status == "pending_validation"
    assert is_feature_enabled(_PRO_FEATURE) is False


def test_devshim_prefix_without_env_stays_locked(monkeypatch):
    """The TPK-DEVSHIM- prefix alone (no env var) must NOT unlock Pro."""
    monkeypatch.delenv("TOKENPAK_LICENSE_DEV_SHIM", raising=False)
    r = activate("TPK-DEVSHIM-PRO-LOCAL-TEST")
    assert r.license.tier == TIER_FREE
    assert is_feature_enabled(_PRO_FEATURE) is False


def test_devshim_env_without_prefix_stays_locked(monkeypatch):
    """The env var alone (ordinary key) must NOT unlock Pro."""
    monkeypatch.setenv("TOKENPAK_LICENSE_DEV_SHIM", "1")
    r = activate("ORDINARY-KEY-WITHOUT-THE-PREFIX-1234")
    assert r.license.tier == TIER_FREE
    assert is_feature_enabled(_PRO_FEATURE) is False


# ── Dev shim: both factors present ───────────────────────────────────────

@pytest.mark.parametrize(
    "key,expected_tier",
    [
        ("TPK-DEVSHIM-PRO-LOCAL-2026", TIER_PRO),
        ("TPK-DEVSHIM-TEAM-LOCAL-2026", TIER_TEAM),
        ("TPK-DEVSHIM-ENTERPRISE-LOCAL-2026", TIER_ENTERPRISE),
        ("TPK-DEVSHIM-LOCAL-2026", TIER_PRO),  # no tier segment → defaults Pro
    ],
)
def test_devshim_activates_requested_tier(key, expected_tier, monkeypatch):
    monkeypatch.setenv("TOKENPAK_LICENSE_DEV_SHIM", "1")
    r = activate(key)
    assert r.ok is True
    assert r.license.tier == expected_tier
    assert r.license.status == "active"
    assert is_feature_enabled(_PRO_FEATURE) is True
    # Persisted: a fresh read reflects the activated tier.
    assert load_license().tier == expected_tier


def test_devshim_then_deactivate_reverts_to_free(monkeypatch):
    monkeypatch.setenv("TOKENPAK_LICENSE_DEV_SHIM", "1")
    activate("TPK-DEVSHIM-PRO-LOCAL-2026")
    assert is_feature_enabled(_PRO_FEATURE) is True
    assert deactivate() is True
    assert load_license().tier == TIER_FREE
    assert is_feature_enabled(_PRO_FEATURE) is False
