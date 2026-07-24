"""Unit tests for route-class compression policy (TIP-05)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tokenpak.services.optimization.protected_spans import SpanType
from tokenpak.services.optimization.route_recipe_policy import (
    ALL_FIDELITY_TIERS,
    ALL_ROUTE_CLASSES,
    DEFAULT_POLICIES,
    FidelityTier,
    RouteClass,
    RoutePolicy,
    apply_policy,
    get_route_policy,
    select_recipes,
)

# ---- DEFAULT_POLICIES coverage --------------------------------------------


def test_every_route_class_has_default_policy():
    missing = [r for r in ALL_ROUTE_CLASSES if r not in DEFAULT_POLICIES]
    assert not missing, f"missing default policies: {missing}"


def test_lossless_routes_protect_their_signature_spans():
    # Diff/debug/test routes must include the expected protected span types.
    git = DEFAULT_POLICIES[RouteClass.GIT_DIFF_REVIEW]
    assert SpanType.DIFF_HUNK_HEADER in git.protected_span_types
    assert SpanType.DIFF_ADDED_REMOVED_LINES in git.protected_span_types
    assert git.lossless_required

    debug = DEFAULT_POLICIES[RouteClass.DEBUGGING]
    assert SpanType.STACK_TRACE_FRAME in debug.protected_span_types
    assert SpanType.EXCEPTION_MESSAGE in debug.protected_span_types
    assert debug.lossless_required


def test_unknown_route_returns_no_optimize_policy():
    p = get_route_policy(None)
    assert p.fidelity == FidelityTier.NO_OPTIMIZE
    p = get_route_policy("")
    assert p.fidelity == FidelityTier.NO_OPTIMIZE
    p = get_route_policy("not_a_real_route")
    assert p.fidelity == FidelityTier.NO_OPTIMIZE
    assert p.recipe_names == ()


def test_unknown_route_class_constant_resolves_to_no_optimize():
    p = get_route_policy(RouteClass.UNKNOWN)
    assert p.fidelity == FidelityTier.NO_OPTIMIZE


def test_route_policy_invariant_lossless_iff_fidelity():
    p = RoutePolicy(
        route_class="x",
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
    )
    assert p.lossless_required is True

    p2 = RoutePolicy(
        route_class="y",
        lossless_required=True,
    )
    assert p2.fidelity == FidelityTier.LOSSLESS_REQUIRED


def test_all_fidelity_tiers_constant_matches_expected_set():
    expected = {
        "lossless_required",
        "semantic_safe",
        "aggressive_ok",
        "cache_response_safe",
        "no_optimize",
    }
    assert ALL_FIDELITY_TIERS == frozenset(expected)


# ---- select_recipes -------------------------------------------------------


@dataclass
class _StubRecipe:
    name: str
    compression_hint: float = 0.0
    operations: List[Dict[str, Any]] = None  # type: ignore[assignment]
    matches_result: bool = True
    match_mode: str = "any"

    def __post_init__(self) -> None:
        if self.operations is None:
            self.operations = []

    def matches(self, *, content_sample: str = "", filename: str = "") -> bool:
        return self.matches_result


class _StubEngine:
    def __init__(self, recipes: Dict[str, _StubRecipe]):
        self._recipes = recipes

    def get_recipe(self, name: str) -> Optional[_StubRecipe]:
        return self._recipes.get(name)


def test_select_recipes_returns_in_policy_order():
    eng = _StubEngine(
        {
            "a": _StubRecipe(name="a"),
            "b": _StubRecipe(name="b"),
            "c": _StubRecipe(name="c"),
        }
    )
    policy = RoutePolicy(
        route_class="x",
        recipe_names=("c", "a", "b"),
    )
    out = select_recipes(policy, engine=eng)
    assert [r.name for r in out] == ["c", "a", "b"]


def test_select_recipes_skips_missing_names_silently():
    eng = _StubEngine({"a": _StubRecipe(name="a")})
    policy = RoutePolicy(
        route_class="x",
        recipe_names=("a", "missing", "also-missing"),
    )
    out = select_recipes(policy, engine=eng)
    assert [r.name for r in out] == ["a"]


def test_select_recipes_drops_overhint_in_lossless_mode():
    eng = _StubEngine(
        {
            "low": _StubRecipe(name="low", compression_hint=0.10),
            "high": _StubRecipe(name="high", compression_hint=0.80),
        }
    )
    policy = RoutePolicy(
        route_class="x",
        recipe_names=("high", "low"),
        lossless_required=True,
        max_lossless_hint=0.20,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
    )
    out = select_recipes(policy, engine=eng)
    assert [r.name for r in out] == ["low"]


def test_select_recipes_keeps_high_hint_when_not_lossless():
    eng = _StubEngine(
        {
            "high": _StubRecipe(name="high", compression_hint=0.80),
        }
    )
    policy = RoutePolicy(
        route_class="x",
        recipe_names=("high",),
        fidelity=FidelityTier.SEMANTIC_SAFE,
    )
    out = select_recipes(policy, engine=eng)
    assert [r.name for r in out] == ["high"]


def test_select_recipes_filters_by_content_match():
    # Only content-mode recipes get matched against content_sample.
    eng = _StubEngine(
        {
            "yes": _StubRecipe(name="yes", match_mode="content", matches_result=True),
            "no": _StubRecipe(name="no", match_mode="content", matches_result=False),
        }
    )
    policy = RoutePolicy(
        route_class="x",
        recipe_names=("yes", "no"),
    )
    out = select_recipes(policy, content_sample="anything", engine=eng)
    assert [r.name for r in out] == ["yes"]


def test_select_recipes_extension_mode_passes_through():
    # Extension-mode recipes should NOT be content-filtered.
    eng = _StubEngine(
        {
            "ext": _StubRecipe(
                name="ext",
                match_mode="extension",
                matches_result=False,
            ),
        }
    )
    policy = RoutePolicy(route_class="x", recipe_names=("ext",))
    out = select_recipes(policy, content_sample="ignored", engine=eng)
    assert [r.name for r in out] == ["ext"]


def test_select_recipes_with_empty_policy_returns_empty():
    out = select_recipes(RoutePolicy(route_class="x"))
    assert out == []


def test_select_recipes_handles_missing_engine_gracefully():
    policy = RoutePolicy(route_class="x", recipe_names=("anything",))

    class _NoneEngine:
        def get_recipe(self, name):
            raise RuntimeError("engine down")

    out = select_recipes(policy, engine=_NoneEngine())
    assert out == []


# ---- apply_policy ---------------------------------------------------------


def _stub_engine(*recipes: _StubRecipe) -> _StubEngine:
    return _StubEngine({r.name: r for r in recipes})


def test_apply_policy_no_optimize_returns_text_unchanged():
    text = "hello world"
    result = apply_policy(text, route_class=RouteClass.UNKNOWN)
    assert result.text == text
    assert result.bytes_saved == 0
    assert not result.applied
    assert result.skipped_reason.startswith("fidelity:")


def test_apply_policy_no_recipes_skips():
    eng = _stub_engine(_StubRecipe(name="a"))
    policy = RoutePolicy(
        route_class="x",
        recipe_names=(),
        fidelity=FidelityTier.SEMANTIC_SAFE,
    )
    result = apply_policy("hello", policy=policy, engine=eng)
    assert not result.applied
    assert result.skipped_reason == "no-recipes-applicable"


def test_apply_policy_compresses_outside_protected_spans():
    # Recipe that asks for whitespace collapse + filler removal.
    recipe = _StubRecipe(
        name="cp-test",
        compression_hint=0.10,
        operations=[
            {"type": "collapse_whitespace"},
            {"type": "remove_filler_phrases"},
        ],
    )
    eng = _stub_engine(recipe)
    text = "Just a quick note.\n\n\nSee /etc/foo.cfg for context.\n\n\nBasically, that's all.\n"
    policy = RoutePolicy(
        route_class="status_check",
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("cp-test",),
        protected_span_types=(SpanType.FILE_PATH,),
    )
    result = apply_policy(text, policy=policy, engine=eng)
    assert result.applied
    assert "/etc/foo.cfg" in result.text  # protected span survived
    assert result.bytes_saved > 0
    assert result.bytes_out < result.bytes_in
    assert "Just " not in result.text or result.text.count("Just ") <= 0
    assert "Basically, " not in result.text


def test_apply_policy_lossless_drops_overhint_recipes():
    too_aggressive = _StubRecipe(
        name="aggressive",
        compression_hint=0.95,
        operations=[{"type": "collapse_whitespace"}],
    )
    eng = _stub_engine(too_aggressive)
    policy = RoutePolicy(
        route_class=RouteClass.GIT_DIFF_REVIEW,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=("aggressive",),
        lossless_required=True,
        max_lossless_hint=0.20,
    )
    result = apply_policy("@@ -1 +1 @@\n hello\n", policy=policy, engine=eng)
    assert not result.applied
    assert result.skipped_reason == "no-recipes-applicable"


def test_apply_policy_skips_when_no_safe_operations():
    # Recipe requests an operation type that is not in the safe whitelist.
    recipe = _StubRecipe(
        name="exotic",
        compression_hint=0.15,
        operations=[{"type": "regex_replace", "pattern": "x", "replacement": "y"}],
    )
    eng = _stub_engine(recipe)
    policy = RoutePolicy(
        route_class="status_check",
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("exotic",),
    )
    result = apply_policy("anything", policy=policy, engine=eng)
    assert not result.applied
    assert result.skipped_reason == "no-safe-operations"


def test_compression_result_to_dict_and_ratio():
    recipe = _StubRecipe(
        name="cp-test",
        compression_hint=0.10,
        operations=[{"type": "collapse_whitespace"}],
    )
    eng = _stub_engine(recipe)
    policy = RoutePolicy(
        route_class="status_check",
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("cp-test",),
    )
    text = "a\n\n\n\n\nb"
    result = apply_policy(text, policy=policy, engine=eng)
    d = result.to_dict()
    assert d["bytes_in"] == result.bytes_in
    assert d["bytes_saved"] == result.bytes_saved
    assert d["recipes_applied"] == ["cp-test"]
    assert 0.0 <= d["ratio"] <= 1.0
    assert result.ratio >= 0.0
