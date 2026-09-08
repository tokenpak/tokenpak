"""Synthetic rate receipts for storage tests; no provider verification claim."""

from datetime import datetime, timezone

from tokenpak.models._pricing import RateBand
from tokenpak.proxy.spend_guard.request_pricing import RequestPrice
from tokenpak.proxy.spend_guard.request_workload import RequestWorkload


def synthetic_price(cost=4.0):
    workload = RequestWorkload(
        body_sha256="a" * 64,
        provider="anthropic",
        model="test-model",
        response_model="test-model",
        input_modality="text",
        output_modality="text",
        cache_ttl_seconds=300,
        service_tier="standard",
        region="global",
        input_tokens=3,
        output_tokens=2,
        cache_read_tokens=1,
        cache_write_tokens=0,
        response_complete=True,
        reason_codes=(),
    )
    band = RateBand(
        "synthetic-storage",
        0,
        cost * 500_000,
        "official",
        "https://platform.claude.com/docs/en/about-claude/pricing",
        cache_read_per_mtok=0,
        cache_write_per_mtok=0,
        max_input_tokens=1000,
        input_modalities=("text",),
        output_modalities=("text",),
        service_tiers=("standard",),
        regions=("global",),
        cache_ttl_seconds=300,
        verified_at="2026-01-01T00:00:00Z",
    )
    return RequestPrice(workload, (band,), datetime.now(timezone.utc).isoformat())
