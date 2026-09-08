"""Malformed workload evidence must not silently select cheaper pricing."""

import pytest

from tokenpak.models import PricingContext, RateBand


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), 10**400])
@pytest.mark.parametrize("field", ["input_per_mtok", "output_per_mtok"])
def test_complete_band_requires_finite_measured_base_rates(value, field):
    kwargs = {
        "band_id": "explicit",
        "input_per_mtok": 2,
        "output_per_mtok": 6,
        "source": "test fixture",
        "source_url": "https://example.com/pricing",
        field: value,
    }
    with pytest.raises(ValueError):
        RateBand(**kwargs)


@pytest.mark.parametrize(
    "group,field",
    [
        ("selectors", "min_input_token"),
        ("rates", "cached_rate"),
        ("provenance", "source_ur1"),
    ],
)
def test_unknown_fields_cannot_disappear_into_a_default_rate(group, field):
    raw = {
        "id": "explicit",
        "selectors": {},
        "rates": {"input": 2, "output": 6},
        "provenance": {"source": "test fixture", "source_url": "https://example.com/pricing"},
    }
    raw[group][field] = "unrecognized"
    with pytest.raises(ValueError, match="unknown fields"):
        RateBand.from_mapping(raw)


def test_missing_context_count_cannot_select_a_threshold_band():
    band = RateBand(
        "long",
        8,
        30,
        "test fixture",
        "https://example.com/pricing",
        min_input_tokens=272001,
    )
    assert not band.matches(PricingContext())
    assert not band.matches(PricingContext(input_tokens=272000))
    assert band.matches(PricingContext(input_tokens=272001))


def test_duplicate_selectors_do_not_hide_ambiguous_metadata():
    with pytest.raises(ValueError, match="unique"):
        RateBand(
            "duplicate",
            2,
            6,
            "test fixture",
            "https://example.com/pricing",
            service_tiers=("standard", "standard"),
        )
