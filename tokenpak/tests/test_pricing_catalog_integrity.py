# SPDX-License-Identifier: Apache-2.0
"""Integrity tests for the model pricing / context-window catalog.

These tests are deliberately model-name-agnostic: they walk the loaded
registry/catalog data structures rather than enumerating specific model
ids, so a future catalog refresh that adds or drops a model can't
silently fall outside what these checks cover. They exist to make catalog
staleness (a new model generation shipping without a priced seed row, or
an unwindowed shadow target) CI-detectable instead of a silent gap that
only surfaces as a wrong or missing cost number downstream.

Not attempted here: a hard reverse-direction gate (every context_windows
entry must have a matching explicit ``models`` row). Several pre-existing,
intentional entries rely on family-rule inference rather than an explicit
seed row (e.g. generic Anthropic aliases like ``claude-opus-4``,
``claude-sonnet-4``) — that's how the registry's family-inference design
is meant to work, so enforcing the reverse direction would false-positive
on working-as-intended data.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from tokenpak.models import get_registry
from tokenpak.telemetry.pricing import _STALENESS_DAYS

_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_SEED_CATALOG_PATH = Path(__file__).parent.parent / "models" / "data" / "seed_catalog.json"


def _seed_catalog_raw() -> dict:
    """Load the raw seed catalog JSON directly (not via the cached registry
    singleton), so these tests see exactly what's committed on disk."""
    return json.loads(_SEED_CATALOG_PATH.read_text(encoding="utf-8"))


class TestCatalogFreshness:
    def test_catalog_freshness(self):
        """_meta.updated must be a real ISO date within the staleness window
        that tokenpak.telemetry.pricing already warns on."""
        raw = _seed_catalog_raw()
        updated = raw.get("_meta", {}).get("updated")
        assert updated, "_meta.updated is missing from the seed catalog"
        updated_date = date.fromisoformat(updated)
        age_days = (date.today() - updated_date).days
        assert 0 <= age_days <= _STALENESS_DAYS, (
            f"_meta.updated ({updated}) is {age_days} days old — refresh the "
            f"catalog and bump _meta.updated (staleness window is "
            f"{_STALENESS_DAYS} days)"
        )


class TestShadowTargetsFullyPriced:
    def test_shadow_targets_are_fully_priced_and_windowed(self):
        """Every shadow-mode target model must resolve to an explicit seed
        pricing row AND a known context window. Shadow requests silently
        compare live traffic against these models — an unpriced or
        unwindowed shadow target would corrupt that comparison without
        raising anywhere."""
        raw = _seed_catalog_raw()
        registry = get_registry()
        shadow_targets = raw.get("shadow_targets", {})
        assert shadow_targets, "seed catalog has no shadow_targets to check"
        for shadow_key, target in shadow_targets.items():
            model = target.get("model", "")
            info = registry.resolve(model)
            assert info.source == "seed", (
                f"shadow target {shadow_key!r} -> {model!r} does not resolve to an "
                f"explicit seed pricing row (source={info.source!r})"
            )
            assert registry.get_max_context(model) is not None, (
                f"shadow target {shadow_key!r} -> {model!r} has no context window"
            )


class TestAllServedModelsPriced:
    def test_all_served_models_have_positive_pricing(self):
        """Every model the registry serves must have a positive input and
        output rate — a zero or missing rate would silently zero out cost
        telemetry for that model."""
        registry = get_registry()
        models = registry.all_models()
        assert models, "registry returned no models"
        for info in models:
            assert info.input_per_mtok > 0, f"{info.model_id}: input_per_mtok must be > 0"
            assert info.output_per_mtok > 0, f"{info.model_id}: output_per_mtok must be > 0"


class TestAnthropicSeedModelsHaveContextWindows:
    def test_anthropic_dateless_seed_models_have_context_windows(self):
        """Every dateless, explicit Anthropic seed row must resolve a
        context window. Scoped to Anthropic only, and to non-date-suffixed
        ids: pre-existing OpenAI/Gemini catalog gaps (newest gpt-5.x and
        gemini-3 rows) are a separate, out-of-scope follow-on, and legacy
        date-suffixed snapshot rows are intentionally not enumerated in
        context_windows (their dateless family alias carries the window
        instead)."""
        raw = _seed_catalog_raw()
        registry = get_registry()
        models_raw = raw.get("models", {})
        checked = 0
        for model_id, data in models_raw.items():
            if data.get("provider") != "anthropic":
                continue
            if _DATE_SUFFIX_RE.search(model_id):
                continue
            checked += 1
            assert registry.get_max_context(model_id) is not None, (
                f"{model_id}: dateless Anthropic seed row has no context window"
            )
        assert checked > 0, "no dateless Anthropic seed rows found to check"
