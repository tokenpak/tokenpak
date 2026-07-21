# SPDX-License-Identifier: Apache-2.0
"""Regression guard — expired / revoked licenses must fail closed.

The single Pro choke point is ``tokenpak.licensing.is_feature_enabled``;
its status gate

    if lic.status != "active":
        return required == TIER_FREE

is the only thing standing between a lapsed/revoked paid license and the
paid feature surface. If that branch is ever removed the gate fails *open*
and an expired Pro license silently unlocks Pro features on public OSS.

What this file pins
-------------------
- Every gated feature returns ``False`` for a ``status="expired"`` license.
- Every gated feature returns ``False`` for a ``status="revoked"`` license.
- A healthy ``status="active"`` Pro license still returns ``True`` for its
  Pro gates — i.e. the fail-closed guard does not over-correct into a
  false-closed regression.

Red-when-broken: delete the ``lic.status != "active"`` branch (gate fails
open) → the expired/revoked assertions flip to ``True`` and fail.

The license is passed explicitly via ``lic=`` at every call, so the gate
never touches the on-disk store or the Pro daemon — fully hermetic.
"""

from __future__ import annotations

import pytest

from tokenpak import licensing
from tokenpak.licensing import (
    TIER_PRO,
    License,
    is_feature_enabled,
)

# Drawn dynamically from the live gate table so the test never hardcodes a
# feature name that might be re-tiered later.
_ALL_GATED_FEATURES = sorted(licensing._GATES)
_PRO_FEATURES = sorted(f for f, tier in licensing._GATES.items() if tier == TIER_PRO)


@pytest.fixture(autouse=True)
def _hermetic_license_store(tmp_path, monkeypatch):
    """Route the license store to a throwaway file.

    Every assertion passes ``lic=`` explicitly so ``load_license`` is never
    reached, but redirecting the store keeps the test inert even if a future
    refactor adds an implicit read path.
    """
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(tmp_path / "license.json"))
    monkeypatch.delenv("TOKENPAK_LICENSE_DEV_SHIM", raising=False)
    yield


@pytest.mark.parametrize("status", ["expired", "revoked"])
@pytest.mark.parametrize("feature", _ALL_GATED_FEATURES)
def test_non_active_license_fails_closed(feature: str, status: str) -> None:
    """A non-active Pro license unlocks no gated feature."""
    lic = License(tier=TIER_PRO, status=status)
    assert is_feature_enabled(feature, lic=lic) is False, (
        f"gate {feature!r} must be closed for a {status} license"
    )


@pytest.mark.parametrize("feature", _PRO_FEATURES)
def test_active_pro_license_enables_pro_gates(feature: str) -> None:
    """A healthy active Pro license still unlocks its Pro gates (no false-closed)."""
    lic = License(tier=TIER_PRO, status="active")
    assert is_feature_enabled(feature, lic=lic) is True, (
        f"active Pro license must enable Pro gate {feature!r}"
    )


def test_gate_table_has_pro_features() -> None:
    """Sanity: the dynamic parametrize sets are non-empty (guards the harness)."""
    assert _ALL_GATED_FEATURES, "expected at least one gated feature"
    assert _PRO_FEATURES, "expected at least one Pro-tier gated feature"
