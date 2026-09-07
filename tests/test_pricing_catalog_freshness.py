# SPDX-License-Identifier: Apache-2.0
"""Freshness and coherence guards for current provider pricing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tokenpak.models import get_rates, translate_model
from tokenpak.models._families import get_sorted_families
from tokenpak.telemetry.cost import SEED_PRICING
from tokenpak.telemetry.pricing import _STALENESS_DAYS, PricingCatalog

ROOT = Path(__file__).resolve().parents[1]
SEED_CATALOG = ROOT / "tokenpak" / "models" / "data" / "seed_catalog.json"


def _seed_pricing(model: str) -> dict:
    for row in SEED_PRICING:
        if row["model"] == model:
            return row
    raise AssertionError(f"missing seed pricing row for {model}")


def test_seed_catalog_is_inside_ci_staleness_window():
    data = json.loads(SEED_CATALOG.read_text(encoding="utf-8"))
    updated = date.fromisoformat(data["_meta"]["updated"])
    assert 0 <= (date.today() - updated).days <= _STALENESS_DAYS


def test_seed_catalog_records_current_official_sources():
    data = json.loads(SEED_CATALOG.read_text(encoding="utf-8"))
    assert data["_meta"]["version"] == "v5"
    assert data["_meta"]["sources"] == [
        {
            "url": "https://platform.claude.com/docs/en/about-claude/pricing",
            "fetched_at": "2026-09-04T15:51:18Z",
        },
        {
            "url": "https://developers.openai.com/api/docs/pricing",
            "fetched_at": "2026-09-04T15:51:18Z",
        },
        {
            "url": "https://developers.openai.com/api/docs/pricing",
            "fetched_at": "2026-09-05T20:31:58Z",
        },
        {
            "url": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
            "fetched_at": "2026-09-05T20:31:58Z",
        },
        {
            "url": "https://developers.openai.com/api/docs/guides/prompt-caching",
            "fetched_at": "2026-09-05T20:31:58Z",
        },
    ]


@pytest.mark.parametrize(
    ("model", "input_rate", "output_rate", "cache_read", "cache_write"),
    [
        ("claude-fable-5-1", 10.0, 50.0, 0.25, 12.5),
        ("claude-fable-5", 10.0, 50.0, 1.0, 12.5),
        ("claude-mythos-5-1", 10.0, 50.0, 0.25, 12.5),
        ("claude-mythos-5", 10.0, 50.0, 1.0, 12.5),
        ("claude-opus-5", 5.0, 25.0, 0.5, 6.25),
        ("claude-opus-4-8", 5.0, 25.0, 0.5, 6.25),
        ("claude-opus-4-7", 5.0, 25.0, 0.5, 6.25),
        ("claude-opus-4-6", 5.0, 25.0, 0.5, 6.25),
        ("claude-opus-4-5", 5.0, 25.0, 0.5, 6.25),
        ("claude-sonnet-5", 2.0, 10.0, 0.2, 2.5),
        ("claude-sonnet-4-6", 3.0, 15.0, 0.3, 3.75),
        ("claude-sonnet-4-5", 3.0, 15.0, 0.3, 3.75),
        ("claude-haiku-4-5", 1.0, 5.0, 0.1, 1.25),
        ("claude-haiku-3-5", 0.8, 4.0, 0.08, 1.0),
        ("gpt-5.6-sol", 4.0, 20.0, 0.4, 5.0),
        ("gpt-5.6-terra", 2.0, 12.0, 0.2, 2.5),
        ("gpt-5.6-luna", 0.2, 1.2, 0.02, 0.25),
        ("gpt-5.5", 5.0, 30.0, 0.5, None),
        ("gpt-5.4", 2.5, 15.0, 0.25, None),
        ("gpt-5.4-mini", 0.75, 4.5, 0.075, None),
        ("gpt-5.4-nano", 0.2, 1.25, 0.02, None),
        ("gpt-5.3-codex", 1.75, 14.0, 0.175, None),
        ("gpt-5.2", 1.75, 14.0, 0.175, None),
        ("gpt-5.1", 1.25, 10.0, 0.125, None),
        ("gpt-5", 1.25, 10.0, 0.125, None),
        ("gpt-5-mini", 0.25, 2.0, 0.025, None),
        ("gpt-5-nano", 0.05, 0.4, 0.005, None),
    ],
)
def test_current_rates_match_every_load_bearing_catalog(
    model: str,
    input_rate: float,
    output_rate: float,
    cache_read: float,
    cache_write: float | None,
):
    registry_rates = get_rates(model)
    assert registry_rates["input"] == input_rate
    assert registry_rates["output"] == output_rate
    assert registry_rates["cached"] == cache_read

    catalog_rates = PricingCatalog.load().get_model(model)
    assert catalog_rates is not None
    assert catalog_rates.input_per_token * 1_000_000 == pytest.approx(input_rate)
    assert catalog_rates.output_per_token * 1_000_000 == pytest.approx(output_rate)
    assert catalog_rates.cache_read_per_token is not None
    assert catalog_rates.cache_read_per_token * 1_000_000 == pytest.approx(cache_read)
    if cache_write is None:
        assert catalog_rates.cache_write_per_token is None
    else:
        assert catalog_rates.cache_write_per_token is not None
        assert catalog_rates.cache_write_per_token * 1_000_000 == pytest.approx(cache_write)

    seed_row = _seed_pricing(model)
    assert seed_row["input_rate"] == input_rate
    assert seed_row["output_rate"] == output_rate


@pytest.mark.parametrize(
    ("variant", "base"),
    [
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-opus-4-8[1m]", "claude-opus-4-8"),
        ("claude-opus-4-7[1m]", "claude-opus-4-7"),
        ("claude-fable-5[1m]", "claude-fable-5"),
    ],
)
def test_live_variants_resolve_without_redundant_aliases(variant: str, base: str):
    assert get_rates(variant) == get_rates(base)


@pytest.mark.parametrize("model", ["claude-opus-4-7", "claude-opus-4-8"])
def test_explicit_opus_rows_preserve_family_translations(model: str):
    assert translate_model(model, "bedrock") == f"anthropic.{model}-v1:0"
    assert translate_model(model, "vertex") == f"{model}@latest"


def test_documented_gpt_5_6_alias_resolves_to_sol():
    assert get_rates("gpt-5.6") == get_rates("gpt-5.6-sol")
    catalog = PricingCatalog.load()
    assert catalog.get_model("gpt-5.6") is catalog.get_model("gpt-5.6-sol")


def test_family_fallbacks_are_anchored_to_current_base_rates():
    families = {rule.pattern: rule for rule in get_sorted_families()}
    opus = families["claude-opus"]
    gpt5 = families["gpt-5"]

    assert (opus.input_per_mtok, opus.output_per_mtok) == (5.0, 25.0)
    assert opus.infer_cache_read(opus.input_per_mtok) == 0.5
    assert (gpt5.input_per_mtok, gpt5.output_per_mtok) == (1.25, 10.0)
    assert gpt5.infer_cache_read(gpt5.input_per_mtok) == 0.125
