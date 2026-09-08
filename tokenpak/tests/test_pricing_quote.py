"""Production quotes bind actual workload applicability and current source evidence."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tokenpak.models import ModelRegistry, PricingQuoteUnavailable, RateBand, quote_workload

AS_OF = datetime(2026, 9, 8, 8, tzinfo=timezone.utc)


def quote(**overrides):
    args = dict(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=10000,
        input_modality="text",
        output_modality="text",
        service_tier="standard",
        region="global",
        cache_ttl_seconds=300,
        as_of=AS_OF,
        max_age_seconds=7 * 86400,
        registry=ModelRegistry(),
    )
    args.update(overrides)
    return quote_workload(**args)


@pytest.mark.parametrize("ttl,write", [(300, 3.75), (3600, 6)])
def test_official_anthropic_cache_tiers_are_complete_and_distinct(ttl, write):
    result = quote(cache_ttl_seconds=ttl)
    assert result.band.input_per_mtok == 3
    assert result.band.output_per_mtok == 15
    assert result.band.cache_read_per_mtok == 0.3
    assert result.band.cache_write_per_mtok == write
    assert result.context.cache_ttl_seconds == ttl
    assert len(result.sha256) == 64
    assert RateBand.from_mapping(result.band.to_mapping()) == result.band


@pytest.mark.parametrize(
    "tokens,inp,out,read,write",
    [
        (272000, 4, 20, 0.4, 5),
        (272001, 8, 30, 0.8, 10),
    ],
)
def test_official_openai_threshold_prices_full_tuple(tokens, inp, out, read, write):
    result = quote(
        provider="openai", model="gpt-5.6-sol", input_tokens=tokens, cache_ttl_seconds=1800
    )
    assert (
        result.band.input_per_mtok,
        result.band.output_per_mtok,
        result.band.cache_read_per_mtok,
        result.band.cache_write_per_mtok,
    ) == (inp, out, read, write)


@pytest.mark.parametrize(
    "model",
    [
        "claude-fable-5-1",
        "claude-fable-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
)
@pytest.mark.parametrize("ttl", [300, 3600])
def test_supported_catalog_models_have_complete_quote(model, ttl):
    result = quote(model=model, cache_ttl_seconds=ttl)
    assert result.model == model
    assert result.band.cache_write_per_mtok is not None
    assert result.band.cache_read_per_mtok is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "claude-sonnet-unknown"},
        {"model": "opus"},
        {"model": "gpt-5.6"},
        {"provider": "bedrock"},
        {"provider": "openai"},
        {"region": "us"},
        {"service_tier": "batch"},
        {"service_tier": "fast"},
        {"input_modality": "image"},
        {"output_modality": "audio"},
        {"cache_ttl_seconds": 1800},
        {"cache_ttl_seconds": None},
        {"cache_ttl_seconds": True},
        {"input_tokens": None},
        {"input_tokens": True},
        {"input_tokens": 1000001},
        {"input_tokens": -1},
        {"region": "*"},
        {"region": "unknown"},
        {"max_age_seconds": 0},
        {"max_age_seconds": True},
        {"as_of": datetime(2026, 9, 8)},
        {"as_of": AS_OF - timedelta(days=1)},
        {"as_of": AS_OF + timedelta(days=8)},
    ],
)
def test_unknown_inferred_or_inapplicable_workload_refuses(overrides):
    with pytest.raises(PricingQuoteUnavailable):
        quote(**overrides)


def test_promotional_price_expiry_is_checked_even_under_long_freshness_policy():
    with pytest.raises(PricingQuoteUnavailable, match="expired"):
        quote(
            provider="openai",
            model="gpt-5.6-sol",
            cache_ttl_seconds=1800,
            as_of=datetime(2026, 11, 22, tzinfo=timezone.utc),
            max_age_seconds=365 * 86400,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"verified_at": None},
        {"source": "inferred"},
        {"source_url": "https://example.com/pricing"},
        {"source_url": "https://user@platform.claude.com/pricing"},
        {"cache_read_per_mtok": None},
        {"cache_write_per_mtok": None},
        {"regions": ("*",)},
        {"cache_ttl_seconds": None},
        {"max_input_tokens": None},
    ],
)
def test_missing_quote_evidence_cannot_fall_back_to_scalar(change):
    registry = ModelRegistry()
    info = registry.resolve("claude-sonnet-4-6")
    registry._models[info.model_id] = replace(
        info, rate_bands=(replace(info.rate_bands[0], **change),)
    )
    with pytest.raises(PricingQuoteUnavailable):
        quote(registry=registry)


def test_ambiguous_catalog_quote_does_not_choose_a_cheaper_band():
    registry = ModelRegistry()
    info = registry.resolve("claude-sonnet-4-6")
    first = info.rate_bands[0]
    registry._models[info.model_id] = replace(
        info, rate_bands=(first, replace(first, band_id="cheaper", input_per_mtok=0.1))
    )
    with pytest.raises(PricingQuoteUnavailable, match="ambiguous"):
        quote(registry=registry)


def test_quote_fingerprint_binds_workload_time_and_prices():
    initial = quote()
    assert initial.sha256 == quote().sha256
    for result in [
        quote(input_tokens=10001),
        quote(cache_ttl_seconds=3600),
        quote(as_of=AS_OF + timedelta(seconds=1)),
    ]:
        assert initial.sha256 != result.sha256


@pytest.mark.parametrize("value", ["2026-09-08", "2026-09-08T08:00:00", "bad", 3])
def test_band_verification_requires_unambiguous_timestamp(value):
    with pytest.raises(ValueError):
        replace(quote().band, verified_at=value)


def test_pricing_expiry_cannot_precede_verification():
    with pytest.raises(ValueError, match="expiry"):
        replace(quote().band, expires_at="2026-09-01T00:00:00Z")
