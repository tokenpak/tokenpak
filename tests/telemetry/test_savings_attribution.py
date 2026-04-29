"""Tests for TIP-06 savings attribution model.

Covers:
- SavingsAttribution source vocabulary enforcement
- parse_openai_usage / parse_anthropic_usage attribution rules
- aggregate_attributions grouping
- format_savings_by_source display
- DB round-trip via TelemetryDB
"""

from __future__ import annotations

import pytest

from tokenpak.telemetry.savings import (
    SourceSummary,
    aggregate_attributions,
    attribution_to_row,
    format_savings_by_source,
    parse_anthropic_usage,
    parse_openai_usage,
)
from tokenpak.telemetry.storage import TelemetryDB
from tokenpak.tip.telemetry_contract import SavingsSource
from tokenpak.tip.trace_contract import SavingsAttribution


# ---------------------------------------------------------------------------
# parse_openai_usage
# ---------------------------------------------------------------------------


def test_parse_openai_usage_with_cached_tokens():
    usage = {
        "prompt_tokens": 70,
        "completion_tokens": 30,
        "prompt_tokens_details": {"cached_tokens": 30},
    }
    results = parse_openai_usage(usage)
    assert len(results) == 1
    attr = results[0]
    assert attr.source == SavingsSource.PLATFORM_CACHE
    assert attr.saved_tokens == 30
    assert attr.credited_to_tokenpak is False


def test_parse_openai_usage_no_cache():
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    results = parse_openai_usage(usage)
    assert results == []


def test_parse_openai_usage_zero_cached_tokens():
    usage = {
        "prompt_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 0},
    }
    results = parse_openai_usage(usage)
    assert results == []


def test_parse_openai_usage_with_pricing():
    usage = {
        "prompt_tokens": 70,
        "prompt_tokens_details": {"cached_tokens": 50},
    }
    pricing = {"input_per_token": 0.000_003}  # $3/M tokens
    results = parse_openai_usage(usage, pricing=pricing)
    assert len(results) == 1
    attr = results[0]
    assert attr.cost_available is True
    assert attr.estimated_cost_saved == pytest.approx(50 * 0.000_003, rel=1e-6)


def test_parse_openai_usage_empty_dict():
    assert parse_openai_usage({}) == []


# ---------------------------------------------------------------------------
# parse_anthropic_usage
# ---------------------------------------------------------------------------


def test_parse_anthropic_usage_with_cache_read():
    usage = {
        "input_tokens": 80,
        "output_tokens": 40,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 0,
    }
    results = parse_anthropic_usage(usage)
    assert len(results) == 1
    attr = results[0]
    assert attr.source == SavingsSource.PROVIDER_PROMPT_CACHE
    assert attr.saved_tokens == 20
    assert attr.credited_to_tokenpak is False


def test_parse_anthropic_usage_no_cache():
    usage = {"input_tokens": 100, "output_tokens": 50}
    results = parse_anthropic_usage(usage)
    assert results == []


def test_parse_anthropic_usage_with_pricing():
    usage = {
        "input_tokens": 80,
        "cache_read_input_tokens": 40,
    }
    pricing = {"input_per_token": 0.000_010}
    results = parse_anthropic_usage(usage, pricing=pricing)
    attr = results[0]
    assert attr.cost_available is True
    # savings_rate = 0.000_010 * 0.90 = 0.000_009; cost = 40 * 0.000_009
    assert attr.estimated_cost_saved == pytest.approx(40 * 0.000_010 * 0.90, rel=1e-6)


# ---------------------------------------------------------------------------
# Attribution rules: provider/platform NEVER credited to TokenPak
# ---------------------------------------------------------------------------


def test_provider_prompt_cache_not_credited_to_tokenpak():
    attr = SavingsAttribution(
        source=SavingsSource.PROVIDER_PROMPT_CACHE,
        saved_tokens=100,
    )
    assert attr.credited_to_tokenpak is False


def test_platform_cache_not_credited_to_tokenpak():
    attr = SavingsAttribution(
        source=SavingsSource.PLATFORM_CACHE,
        saved_tokens=50,
    )
    assert attr.credited_to_tokenpak is False


def test_tokenpak_semantic_cache_credited():
    attr = SavingsAttribution(
        source=SavingsSource.TOKENPAK_SEMANTIC_CACHE,
        saved_tokens=200,
    )
    assert attr.credited_to_tokenpak is True


def test_tokenpak_compression_credited():
    attr = SavingsAttribution(
        source=SavingsSource.TOKENPAK_COMPRESSION,
        saved_tokens=150,
    )
    assert attr.credited_to_tokenpak is True


def test_unattributed_not_credited():
    attr = SavingsAttribution(
        source=SavingsSource.UNATTRIBUTED,
        saved_tokens=10,
    )
    assert attr.credited_to_tokenpak is False


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="Unknown savings source"):
        SavingsAttribution(source="invented_source", saved_tokens=1)


# ---------------------------------------------------------------------------
# aggregate_attributions
# ---------------------------------------------------------------------------


def test_aggregate_single_source():
    attrs = [
        SavingsAttribution(source=SavingsSource.TOKENPAK_COMPRESSION, saved_tokens=100),
        SavingsAttribution(source=SavingsSource.TOKENPAK_COMPRESSION, saved_tokens=50),
    ]
    by_source = aggregate_attributions(attrs)
    assert SavingsSource.TOKENPAK_COMPRESSION in by_source
    assert by_source[SavingsSource.TOKENPAK_COMPRESSION].saved_tokens == 150
    assert by_source[SavingsSource.TOKENPAK_COMPRESSION].request_count == 2


def test_aggregate_multiple_sources():
    attrs = [
        SavingsAttribution(source=SavingsSource.TOKENPAK_COMPRESSION, saved_tokens=100),
        SavingsAttribution(source=SavingsSource.PLATFORM_CACHE, saved_tokens=200),
        SavingsAttribution(source=SavingsSource.PROVIDER_PROMPT_CACHE, saved_tokens=50),
    ]
    by_source = aggregate_attributions(attrs)
    assert len(by_source) == 3
    tp_managed = sum(
        s.saved_tokens for s in by_source.values() if s.credited_to_tokenpak
    )
    non_tp = sum(
        s.saved_tokens for s in by_source.values() if not s.credited_to_tokenpak
    )
    assert tp_managed == 100
    assert non_tp == 250


def test_aggregate_empty():
    assert aggregate_attributions([]) == {}


# ---------------------------------------------------------------------------
# format_savings_by_source
# ---------------------------------------------------------------------------


def test_format_savings_by_source_shows_separator():
    by_source = {
        SavingsSource.TOKENPAK_COMPRESSION: SourceSummary(
            source=SavingsSource.TOKENPAK_COMPRESSION,
            saved_tokens=500,
            credited_to_tokenpak=True,
        ),
        SavingsSource.PLATFORM_CACHE: SourceSummary(
            source=SavingsSource.PLATFORM_CACHE,
            saved_tokens=300,
            credited_to_tokenpak=False,
        ),
    }
    output = format_savings_by_source(by_source)
    assert "TokenPak-managed" in output
    assert "Provider/Platform" in output
    assert "not overclaimed" in output


def test_format_savings_by_source_empty():
    output = format_savings_by_source({})
    assert "No savings" in output


# ---------------------------------------------------------------------------
# TelemetryDB round-trip
# ---------------------------------------------------------------------------


def test_db_insert_and_query_savings_attribution():
    db = TelemetryDB(":memory:")
    rows = [
        attribution_to_row(
            "req-1",
            SavingsAttribution(
                source=SavingsSource.TOKENPAK_COMPRESSION,
                saved_tokens=200,
                raw_tokens=1000,
                sent_tokens=800,
            ),
            model="claude-sonnet-4-6",
        ),
        attribution_to_row(
            "req-1",
            SavingsAttribution(
                source=SavingsSource.PLATFORM_CACHE,
                saved_tokens=100,
                raw_tokens=1000,
                sent_tokens=900,
            ),
            model="claude-sonnet-4-6",
        ),
    ]
    db.batch_insert_savings_attributions(rows)

    summary = db.query_savings_by_source(days=7)
    sources = {r["source"] for r in summary}
    assert SavingsSource.TOKENPAK_COMPRESSION in sources
    assert SavingsSource.PLATFORM_CACHE in sources


def test_db_tokenpak_savings_not_mixed_with_provider_cache():
    db = TelemetryDB(":memory:")

    tp_row = attribution_to_row(
        "req-2",
        SavingsAttribution(
            source=SavingsSource.TOKENPAK_SEMANTIC_CACHE,
            saved_tokens=300,
        ),
    )
    tp_row["credited_to_tokenpak"] = 1

    provider_row = attribution_to_row(
        "req-2",
        SavingsAttribution(
            source=SavingsSource.PROVIDER_PROMPT_CACHE,
            saved_tokens=150,
        ),
    )
    provider_row["credited_to_tokenpak"] = 0

    db.batch_insert_savings_attributions([tp_row, provider_row])
    summary = db.query_savings_by_source(days=7)

    by_source = {r["source"]: r for r in summary}
    assert by_source[SavingsSource.TOKENPAK_SEMANTIC_CACHE]["credited_to_tokenpak"] == 1
    assert by_source[SavingsSource.PROVIDER_PROMPT_CACHE]["credited_to_tokenpak"] == 0
