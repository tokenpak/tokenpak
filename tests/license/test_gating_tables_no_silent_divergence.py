# SPDX-License-Identifier: Apache-2.0
"""Regression guard against silent divergence between the two Pro
feature-gating tables in this repository.

Background
----------

``tokenpak.licensing`` (`_GATES` / `is_feature_enabled`) is the in-repo
choke point for Pro gating, used by the OSS `tokenpak features` CLI
surface. ``tokenpak.agent.license.validator`` (`TIER_FEATURES` /
`required_tier_for`) is a *separate* table, kept because it is an external
contract: the private `tokenpak-paid` package imports `TIER_FEATURES` and
`LicenseTier` directly (see `tokenpak_paid.entitlements._tier_grants` and
`required_tier_for`) to gate real paid CLI commands across ~25 command
wrappers. Deleting or renaming the validator-side table from this repo
alone would silently break every one of those commands for real Pro
customers, so it is intentionally kept rather than merged away.

The two vocabularies use different feature-ID schemes (coded OSS IDs like
``C3_code_compression`` vs. coarse externally-visible names like
``compression_advanced``) and are not a confirmed 1:1 mapping today, with
one exception verified by reading every current `tokenpak-paid` call
site: ``ab_testing`` (validator-side) and ``X1_ab_testing`` (licensing
-side) are the same capability.

What this test suite guards, given it cannot itself resolve the remaining
mapping (that needs product/pricing input this repo alone can't supply):

1. The one confirmed overlapping feature keeps agreeing, at the level
   that actually matters — the accept/reject decision a real caller gets
   back, across every tier in the product's ladder, not just a static
   tier-string comparison.
2. Neither table silently grows a second populated paid tier without the
   other knowing about it. The product collapsed to exactly two tiers
   (free, pro); a change that starts gating something at a third tier on
   one side while the other side's ladder still only knows free/pro is
   exactly the "one table has no entry for a tier the other invented"
   divergence class this audit finding is about.
"""

from __future__ import annotations

from tokenpak.agent.license.validator import (
    TIER_FEATURES,
    LicenseTier,
    required_tier_for,
)
from tokenpak.licensing import _GATES, TIER_FREE, TIER_PRO, License, is_feature_enabled

#: The one feature name confirmed — by reading every current `tokenpak-paid`
#: command wrapper — to mean the same conceptual capability under both
#: vocabularies. Do not add to this dict without similarly confirming the
#: mapping against the actual `tokenpak-paid` call sites; guessing a mapping
#: from name similarity alone is exactly the mistake this test exists to
#: prevent.
CONFIRMED_CROSS_REFERENCE = {
    # validator.py name -> licensing._GATES name
    "ab_testing": "X1_ab_testing",
}


def test_confirmed_cross_reference_feature_exists_on_both_sides() -> None:
    for old_id, new_id in CONFIRMED_CROSS_REFERENCE.items():
        assert old_id in TIER_FEATURES[LicenseTier.PRO], (
            f"{old_id!r} was removed from validator.TIER_FEATURES; "
            "tokenpak-paid still names it — do not delete without "
            "coordinating with that package first"
        )
        assert new_id in _GATES, (
            f"{new_id!r} was removed from licensing._GATES; it is the "
            f"confirmed OSS-side counterpart of validator-side {old_id!r}"
        )


def test_confirmed_cross_reference_agrees_on_static_tier() -> None:
    for old_id, new_id in CONFIRMED_CROSS_REFERENCE.items():
        old_tier = required_tier_for(old_id)
        new_tier = _GATES[new_id]
        assert old_tier == new_tier, (
            f"{old_id!r} (validator) requires tier {old_tier!r} but its "
            f"confirmed counterpart {new_id!r} (licensing) requires "
            f"{new_tier!r} — the two choke points have diverged"
        )


def test_confirmed_cross_reference_agrees_on_live_decision_every_tier() -> None:
    """Actually execute both choke points, not just compare tier strings.

    This is the stronger check: for the one confirmed shared feature, a
    Free-tier license and a Pro-tier license must get the *same*
    accept/reject answer whether asked through
    ``licensing.is_feature_enabled`` or through the validator-side ladder
    that ``tokenpak_paid.required_tier_for`` builds its decision from.
    """
    ladder = LicenseTier.ladder()
    for old_id, new_id in CONFIRMED_CROSS_REFERENCE.items():
        needed = LicenseTier(required_tier_for(old_id))
        for tier_value in (TIER_FREE, TIER_PRO):
            lic = License(tier=tier_value, status="active")
            new_side = is_feature_enabled(new_id, lic=lic)
            held = LicenseTier(tier_value)
            old_side = ladder.index(held) >= ladder.index(needed)
            assert new_side == old_side, (
                f"{old_id!r}/{new_id!r} diverge at tier={tier_value!r}: "
                f"licensing.is_feature_enabled={new_side} vs. "
                f"validator-ladder decision={old_side}"
            )


def test_neither_table_has_silently_grown_a_third_populated_tier() -> None:
    """Both tables must keep gating exactly the single paid tier, `pro`.

    The product ladder is free < pro on both sides. If either table starts
    gating a feature at some other tier value without the other table's
    ladder being updated to match, `required_tier_for` / `is_feature_enabled`
    stop being comparable at all — this is the structural precondition the
    cross-reference tests above rely on, so it is asserted directly.
    """
    assert set(_GATES.values()) <= {TIER_FREE, TIER_PRO}

    populated_tiers = {tier for tier, feats in TIER_FEATURES.items() if feats}
    assert populated_tiers <= {LicenseTier.PRO}
