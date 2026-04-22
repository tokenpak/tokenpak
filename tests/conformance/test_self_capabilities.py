"""Self-published capability profile compliance.

tokenpak.core.contracts.capabilities is the single canonical source
for what the reference implementation publishes. These tests assert
that source satisfies the TIP-1.0 profile requirements for the two
profiles tokenpak claims per Constitution §13.3.
"""
from __future__ import annotations

import pytest

from tokenpak_tip_validator import validate_capability_set, validate_profile

from tokenpak.core.contracts.capabilities import (
    SELF_CAPABILITIES_COMPANION,
    SELF_CAPABILITIES_PROXY,
    SELF_PROFILES,
)


pytestmark = pytest.mark.conformance


def test_self_profiles_is_the_canonical_pair():
    assert SELF_PROFILES == ("tip-proxy", "tip-companion")


def test_proxy_capability_set_is_well_formed():
    result = validate_capability_set(list(SELF_CAPABILITIES_PROXY))
    assert result.ok, (
        f"SELF_CAPABILITIES_PROXY has ill-formed labels: "
        f"{[(f.code, f.message) for f in result.errors()]}"
    )


def test_companion_capability_set_is_well_formed():
    result = validate_capability_set(list(SELF_CAPABILITIES_COMPANION))
    assert result.ok, (
        f"SELF_CAPABILITIES_COMPANION has ill-formed labels: "
        f"{[(f.code, f.message) for f in result.errors()]}"
    )


def test_proxy_profile_requirements_satisfied():
    result = validate_profile(
        "tip-proxy", capabilities=list(SELF_CAPABILITIES_PROXY)
    )
    errors = [f for f in result.errors()]
    assert not errors, (
        f"tip-proxy profile unmet by SELF_CAPABILITIES_PROXY: "
        f"{[(f.code, f.message) for f in errors]}"
    )


def test_companion_profile_requirements_satisfied():
    result = validate_profile(
        "tip-companion", capabilities=list(SELF_CAPABILITIES_COMPANION)
    )
    errors = [f for f in result.errors()]
    assert not errors, (
        f"tip-companion profile unmet by SELF_CAPABILITIES_COMPANION: "
        f"{[(f.code, f.message) for f in errors]}"
    )
