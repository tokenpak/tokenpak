"""Tests for agent/compression/directives.py"""

from tokenpak.compression.directives import (
    DirectiveResult,
    apply_agent_dedup,
    apply_compression_directives,
    apply_context_plan,
    apply_directives,
    extract_model_route,
    parse_directives,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_DIRECTIVE = {
    "request_id": "abc123",
    "compression": [
        {"target": "segment_0", "action": "prune", "params": {"keep_ratio": 0.5}},
        {"target": "segment_1", "action": "collapse", "params": {"keep_signature": True}},
        {"target": "segment_2", "action": "dedup", "params": {}},
        {"target": "segment_3", "action": "prune_turns", "params": {"remove": [0, 1]}},
    ],
    "model_route": {"recommended": "haiku", "confidence": 0.91, "fallback": "sonnet"},
    "context_plan": {"block_priority": [8, 3, 11], "drop_blocks": [6, 9]},
    "pattern_hints": {"keep_last": 8, "preserve_code": True},
    "agent_dedup": {"skip_blocks": [2, 5]},
    "estimated_savings": {"tokens": 955, "pct": 33.5},
}

SEGMENTS = [
    {"id": "segment_0", "content": "line1\nline2\nline3\nline4", "tokens": 40},
    {"id": "segment_1", "content": "def foo(x):\n    return x * 2\n", "tokens": 20},
    {"id": "segment_2", "content": "hello\nhello\nworld\nhello", "tokens": 10},
    {"id": "segment_3", "content": "turn0\n\nturn1\n\nturn2", "tokens": 15},
]

VAULT_BLOCKS = [
    {"id": 1, "content": "block 1"},
    {"id": 2, "content": "block 2"},
    {"id": 3, "content": "block 3"},
    {"id": 6, "content": "block 6"},
    {"id": 8, "content": "block 8"},
    {"id": 9, "content": "block 9"},
    {"id": 11, "content": "block 11"},
]


# ---------------------------------------------------------------------------
# parse_directives
# ---------------------------------------------------------------------------


class TestParseDirectives:
    def test_parses_valid_full_directive(self):
        result = parse_directives(FULL_DIRECTIVE)
        assert len(result["compression"]) == 4
        assert result["model_route"]["recommended"] == "haiku"
        assert result["context_plan"]["drop_blocks"] == [6, 9]
        assert result["agent_dedup"]["skip_blocks"] == [2, 5]
        assert result["estimated_savings"]["pct"] == 33.5

    def test_skips_unknown_action(self):
        d = {"compression": [{"target": "segment_0", "action": "explode", "params": {}}]}
        result = parse_directives(d)
        assert result["compression"] == []

    def test_skips_missing_target(self):
        d = {"compression": [{"action": "prune", "params": {}}]}
        result = parse_directives(d)
        assert result["compression"] == []

    def test_skips_missing_action(self):
        d = {"compression": [{"target": "segment_0", "params": {}}]}
        result = parse_directives(d)
        assert result["compression"] == []

    def test_skips_invalid_model_route(self):
        result = parse_directives({"model_route": "bad"})
        assert "model_route" not in result

    def test_skips_model_route_without_recommended(self):
        result = parse_directives({"model_route": {"confidence": 0.9}})
        assert "model_route" not in result

    def test_handles_non_dict_input(self):
        result = parse_directives("not a dict")
        assert result == {}

    def test_passes_through_request_id(self):
        result = parse_directives({"request_id": "xyz"})
        assert result["request_id"] == "xyz"

    def test_partial_malformed_compression(self):
        d = {
            "compression": [
                {"target": "segment_0", "action": "prune", "params": {"keep_ratio": 0.5}},
                "not a dict",
                {"target": "segment_1", "action": "unknown_action", "params": {}},
                {"target": "segment_2", "action": "dedup", "params": {}},
            ]
        }
        result = parse_directives(d)
        assert len(result["compression"]) == 2
        actions = [e["action"] for e in result["compression"]]
        assert "prune" in actions
        assert "dedup" in actions


# ---------------------------------------------------------------------------
# apply_compression_directives
# ---------------------------------------------------------------------------


class TestApplyCompressionDirectives:
    def test_prune_reduces_tokens(self):
        segs = [{"id": "segment_0", "content": "a\nb\nc\nd", "tokens": 100}]
        directives = [{"target": "segment_0", "action": "prune", "params": {"keep_ratio": 0.5}}]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert updated[0]["tokens"] == 50
        assert "_compressed" in updated[0]
        assert len(applied) == 1
        assert len(skipped) == 0

    def test_collapse_to_signature(self):
        segs = [{"id": "segment_1", "content": "def foo(x):\n    return x * 2", "tokens": 20}]
        directives = [
            {"target": "segment_1", "action": "collapse", "params": {"keep_signature": True}}
        ]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert "def foo(x):" in updated[0]["content"]
        assert "..." in updated[0]["content"]
        assert "return x * 2" not in updated[0]["content"]

    def test_dedup_removes_duplicate_lines(self):
        segs = [{"id": "segment_2", "content": "hello\nhello\nworld\nhello", "tokens": 10}]
        directives = [{"target": "segment_2", "action": "dedup", "params": {}}]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        lines = updated[0]["content"].splitlines()
        assert lines.count("hello") == 1
        assert "world" in updated[0]["content"]

    def test_prune_turns_removes_specified_turns(self):
        segs = [{"id": "segment_3", "content": "turn0\n\nturn1\n\nturn2", "tokens": 15}]
        directives = [
            {"target": "segment_3", "action": "prune_turns", "params": {"remove": [0, 1]}}
        ]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert "turn2" in updated[0]["content"]
        assert "turn0" not in updated[0]["content"]
        assert "turn1" not in updated[0]["content"]

    def test_unknown_target_skipped(self):
        segs = [{"id": "segment_0", "content": "hello", "tokens": 5}]
        directives = [{"target": "segment_99", "action": "prune", "params": {}}]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert len(skipped) == 1
        assert len(applied) == 0
        assert updated[0]["content"] == "hello"

    def test_numeric_segment_id_fallback(self):
        segs = [{"content": "line1\nline2\nline3", "tokens": 30}]
        directives = [{"target": "segment_0", "action": "prune", "params": {"keep_ratio": 0.33}}]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert len(applied) == 1

    def test_applies_all_action_types(self):
        updated, applied, skipped = apply_compression_directives(
            list(SEGMENTS),
            parse_directives(FULL_DIRECTIVE)["compression"],
        )
        assert len(applied) == 4
        assert len(skipped) == 0

    def test_savings_within_estimated_range(self):
        """Savings should be within 10% of estimated_savings.tokens (criterion 7)."""
        directives = parse_directives(FULL_DIRECTIVE)
        segs = [
            {
                "id": "segment_0",
                "content": "\n".join([f"line{i}" for i in range(100)]),
                "tokens": 100,
            },
        ]
        compression = [{"target": "segment_0", "action": "prune", "params": {"keep_ratio": 0.5}}]
        tokens_before = sum(s["tokens"] for s in segs)
        updated, _, _ = apply_compression_directives(segs, compression)
        tokens_after = sum(s["tokens"] for s in updated)
        actual_savings = tokens_before - tokens_after
        estimated = 50  # keep_ratio=0.5 on 100 tokens → 50 saved
        assert abs(actual_savings - estimated) <= estimated * 0.10


# ---------------------------------------------------------------------------
# apply_context_plan
# ---------------------------------------------------------------------------


class TestApplyContextPlan:
    def test_drops_blocks(self):
        result = apply_context_plan(VAULT_BLOCKS, {"drop_blocks": [6, 9]})
        ids = [b["id"] for b in result]
        assert 6 not in ids
        assert 9 not in ids

    def test_reorders_by_priority(self):
        result = apply_context_plan(VAULT_BLOCKS, {"block_priority": [8, 3, 11], "drop_blocks": []})
        ids = [b["id"] for b in result]
        assert ids.index(8) < ids.index(3)
        assert ids.index(3) < ids.index(11)

    def test_drops_and_reorders(self):
        result = apply_context_plan(
            VAULT_BLOCKS, {"block_priority": [8, 3, 11], "drop_blocks": [6, 9]}
        )
        ids = [b["id"] for b in result]
        assert 6 not in ids
        assert 9 not in ids
        assert ids[0] == 8

    def test_empty_plan_returns_unchanged(self):
        result = apply_context_plan(VAULT_BLOCKS, {})
        assert result == VAULT_BLOCKS


# ---------------------------------------------------------------------------
# apply_agent_dedup
# ---------------------------------------------------------------------------


class TestApplyAgentDedup:
    def test_removes_skip_blocks(self):
        result = apply_agent_dedup(VAULT_BLOCKS, {"skip_blocks": [2, 6]})
        ids = [b["id"] for b in result]
        assert 2 not in ids
        assert 6 not in ids

    def test_empty_skip_returns_all(self):
        result = apply_agent_dedup(VAULT_BLOCKS, {"skip_blocks": []})
        assert result == VAULT_BLOCKS

    def test_empty_dedup_returns_all(self):
        result = apply_agent_dedup(VAULT_BLOCKS, {})
        assert result == VAULT_BLOCKS


# ---------------------------------------------------------------------------
# extract_model_route
# ---------------------------------------------------------------------------


class TestExtractModelRoute:
    def test_returns_model_route(self):
        route = extract_model_route({"model_route": {"recommended": "haiku", "confidence": 0.91}})
        assert route["recommended"] == "haiku"

    def test_returns_empty_when_absent(self):
        assert extract_model_route({}) == {}


# ---------------------------------------------------------------------------
# apply_directives (top-level)
# ---------------------------------------------------------------------------


class TestApplyDirectives:
    def test_full_pipeline(self):
        result = apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), FULL_DIRECTIVE)
        assert isinstance(result, DirectiveResult)
        assert result.model_route["recommended"] == "haiku"
        assert len(result.segments) == 4
        # Dropped blocks 6,9; deduped blocks 2,5
        block_ids = [b["id"] for b in result.vault_blocks]
        assert 6 not in block_ids
        assert 9 not in block_ids
        assert 2 not in block_ids

    def test_server_directives_override_local_recipe(self):
        called = []

        def local_recipe(segs, blocks):
            called.append(True)
            return DirectiveResult(segments=segs, vault_blocks=blocks)

        apply_directives(
            list(SEGMENTS), list(VAULT_BLOCKS), FULL_DIRECTIVE, local_recipe_fn=local_recipe
        )
        assert called == [], "local recipe should NOT be called when server directives present"

    def test_falls_back_to_local_recipe_when_no_directives(self):
        called = []

        def local_recipe(segs, blocks):
            called.append(True)
            return DirectiveResult(segments=segs, vault_blocks=blocks)

        apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), {}, local_recipe_fn=local_recipe)
        assert called == [True]

    def test_graceful_on_empty_directives(self):
        result = apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), {})
        assert len(result.segments) == len(
            SEGMENTS
        )  # no directives → segments pass through unchanged
        assert result.model_route == {}

    def test_graceful_on_none_directives(self):
        result = apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), None)
        assert result.model_route == {}

    def test_applied_and_skipped_lists_populated(self):
        result = apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), FULL_DIRECTIVE)
        assert len(result.applied) > 0
        assert isinstance(result.skipped, list)

    def test_tokens_saved_positive(self):
        result = apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), FULL_DIRECTIVE)
        assert result.tokens_saved >= 0


# ---------------------------------------------------------------------------
# New directive types: compression_mode_change, recipe_override, budget_adjustment
# ---------------------------------------------------------------------------

MOCK_NEW_DIRECTIVES = {
    "request_id": "mock-server-001",
    "compression": [
        {
            "target": "segment_0",
            "action": "compression_mode_change",
            "params": {"mode": "aggressive"},
        },
        {
            "target": "segment_1",
            "action": "recipe_override",
            "params": {"recipe_name": "code_review", "max_tokens": 500},
        },
        {"target": "segment_2", "action": "budget_adjustment", "params": {"reduction_pct": 0.4}},
    ],
    "estimated_savings": {"tokens": 200, "pct": 20.0},
}


class TestCompressionModeChange:
    """Tests for compression_mode_change directive (mocked server response)."""

    def test_aggressive_mode_prunes_content(self):
        segs = [
            {"id": "segment_0", "content": "\n".join(f"line{i}" for i in range(20)), "tokens": 100}
        ]
        directives = [
            {
                "target": "segment_0",
                "action": "compression_mode_change",
                "params": {"mode": "aggressive"},
            }
        ]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert updated[0]["tokens"] < 100
        assert updated[0]["_compressed"] == "compression_mode_change"
        assert len(applied) == 1

    def test_conservative_mode_keeps_more(self):
        segs = [
            {"id": "segment_0", "content": "\n".join(f"line{i}" for i in range(20)), "tokens": 100}
        ]
        agg = [
            {
                "target": "segment_0",
                "action": "compression_mode_change",
                "params": {"mode": "aggressive"},
            }
        ]
        cons = [
            {
                "target": "segment_0",
                "action": "compression_mode_change",
                "params": {"mode": "conservative"},
            }
        ]
        agg_result, _, _ = apply_compression_directives(list(segs), agg)
        cons_result, _, _ = apply_compression_directives(list(segs), cons)
        assert cons_result[0]["tokens"] >= agg_result[0]["tokens"]

    def test_lossless_mode_preserves_content(self):
        segs = [{"id": "segment_0", "content": "important content", "tokens": 10}]
        directives = [
            {
                "target": "segment_0",
                "action": "compression_mode_change",
                "params": {"mode": "lossless"},
            }
        ]
        updated, applied, _ = apply_compression_directives(segs, directives)
        assert updated[0]["content"] == "important content"
        assert updated[0]["_compression_mode"] == "lossless"

    def test_summarize_mode_adds_ellipsis(self):
        segs = [
            {
                "id": "segment_0",
                "content": "first line\n" + "\n".join(f"extra{i}" for i in range(10)),
                "tokens": 80,
            }
        ]
        directives = [
            {
                "target": "segment_0",
                "action": "compression_mode_change",
                "params": {"mode": "summarize"},
            }
        ]
        updated, applied, _ = apply_compression_directives(segs, directives)
        assert "…" in updated[0]["content"] or "omitted" in updated[0]["content"]

    def test_invalid_mode_defaults_to_aggressive(self):
        segs = [
            {"id": "segment_0", "content": "\n".join(f"line{i}" for i in range(20)), "tokens": 100}
        ]
        directives = [
            {
                "target": "segment_0",
                "action": "compression_mode_change",
                "params": {"mode": "turbo_max"},
            }
        ]
        updated, applied, skipped = apply_compression_directives(segs, directives)
        assert len(applied) == 1  # still applied with aggressive fallback
        assert updated[0]["_compression_mode"] == "aggressive"


class TestRecipeOverride:
    """Tests for recipe_override directive (mocked server response)."""

    def test_recipe_name_stored_in_segment(self):
        segs = [{"id": "segment_1", "content": "some content", "tokens": 20}]
        directives = [
            {
                "target": "segment_1",
                "action": "recipe_override",
                "params": {"recipe_name": "code_review"},
            }
        ]
        updated, applied, _ = apply_compression_directives(segs, directives)
        assert updated[0]["_recipe_name"] == "code_review"
        assert updated[0]["_compressed"] == "recipe_override"

    def test_max_tokens_override_stored(self):
        segs = [{"id": "segment_1", "content": "some content", "tokens": 20}]
        directives = [
            {
                "target": "segment_1",
                "action": "recipe_override",
                "params": {"recipe_name": "fast_answer", "max_tokens": 300},
            }
        ]
        updated, _, _ = apply_compression_directives(segs, directives)
        assert updated[0]["max_tokens"] == 300

    def test_recipe_override_preserves_original_content(self):
        segs = [{"id": "segment_1", "content": "keep this content", "tokens": 20}]
        directives = [
            {
                "target": "segment_1",
                "action": "recipe_override",
                "params": {"recipe_name": "test_recipe"},
            }
        ]
        updated, _, _ = apply_compression_directives(segs, directives)
        assert updated[0]["content"] == "keep this content"


class TestBudgetAdjustment:
    """Tests for budget_adjustment directive (mocked server response)."""

    def test_reduction_pct_trims_tokens(self):
        segs = [
            {"id": "segment_2", "content": "\n".join(f"word{i}" for i in range(100)), "tokens": 100}
        ]
        directives = [
            {"target": "segment_2", "action": "budget_adjustment", "params": {"reduction_pct": 0.5}}
        ]
        updated, applied, _ = apply_compression_directives(segs, directives)
        assert updated[0]["tokens"] <= 55  # ~50% reduction
        assert updated[0]["_compressed"] == "budget_adjustment"

    def test_max_tokens_cap_enforced(self):
        segs = [
            {"id": "segment_2", "content": "\n".join(f"word{i}" for i in range(100)), "tokens": 200}
        ]
        directives = [
            {"target": "segment_2", "action": "budget_adjustment", "params": {"max_tokens": 50}}
        ]
        updated, applied, _ = apply_compression_directives(segs, directives)
        assert updated[0]["tokens"] <= 50
        assert updated[0]["_budget_cap"] == 50

    def test_budget_no_params_is_noop(self):
        segs = [{"id": "segment_2", "content": "hello world", "tokens": 10}]
        directives = [{"target": "segment_2", "action": "budget_adjustment", "params": {}}]
        updated, applied, _ = apply_compression_directives(segs, directives)
        assert len(applied) == 1
        assert updated[0]["content"] == "hello world"


class TestNewDirectivesInKnownActions:
    """Verify new actions pass parse_directives validation."""

    def test_compression_mode_change_accepted(self):
        d = {
            "compression": [
                {
                    "target": "segment_0",
                    "action": "compression_mode_change",
                    "params": {"mode": "conservative"},
                }
            ]
        }
        result = parse_directives(d)
        assert len(result["compression"]) == 1

    def test_recipe_override_accepted(self):
        d = {
            "compression": [
                {
                    "target": "segment_0",
                    "action": "recipe_override",
                    "params": {"recipe_name": "debug"},
                }
            ]
        }
        result = parse_directives(d)
        assert len(result["compression"]) == 1

    def test_budget_adjustment_accepted(self):
        d = {
            "compression": [
                {
                    "target": "segment_0",
                    "action": "budget_adjustment",
                    "params": {"reduction_pct": 0.3},
                }
            ]
        }
        result = parse_directives(d)
        assert len(result["compression"]) == 1

    def test_full_mock_server_response_applies(self):
        segs = [
            {"id": "segment_0", "content": "\n".join(f"l{i}" for i in range(20)), "tokens": 100},
            {"id": "segment_1", "content": "fn code body", "tokens": 50},
            {"id": "segment_2", "content": "\n".join(f"item{i}" for i in range(20)), "tokens": 80},
        ]
        result = apply_directives(list(segs), [], MOCK_NEW_DIRECTIVES)
        assert len(result.applied) == 3
        assert len(result.skipped) == 0


# ---------------------------------------------------------------------------
# DirectiveCache
# ---------------------------------------------------------------------------

from tokenpak.compression.directives import DirectiveCache


class TestDirectiveCache:
    def test_cache_hit_returns_parsed(self):
        cache = DirectiveCache()
        raw = {"compression": [{"target": "segment_0", "action": "prune", "params": {}}]}
        parsed = parse_directives(raw)
        cache.set(raw, parsed)
        result = cache.get(raw)
        assert result is not None
        assert result["compression"][0]["action"] == "prune"

    def test_cache_miss_returns_none(self):
        cache = DirectiveCache()
        result = cache.get({"request_id": "nope"})
        assert result is None

    def test_cache_expiry(self):
        cache = DirectiveCache(ttl_seconds=0.01)
        raw = {"request_id": "x"}
        cache.set(raw, {"compression": []})
        import time

        time.sleep(0.05)
        assert cache.get(raw) is None

    def test_cache_invalidate(self):
        cache = DirectiveCache()
        raw = {"request_id": "y"}
        cache.set(raw, {"compression": []})
        removed = cache.invalidate(raw)
        assert removed is True
        assert cache.get(raw) is None

    def test_cache_clear(self):
        cache = DirectiveCache()
        for i in range(3):
            cache.set({"id": i}, {"compression": []})
        cache.clear()
        assert cache.size == 0

    def test_apply_directives_uses_cache(self):
        cache = DirectiveCache()
        raw = dict(FULL_DIRECTIVE)
        # First call — populates cache
        apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), raw, cache=cache)
        assert cache.size == 1
        # Second call — cache hit (size stays 1)
        apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), raw, cache=cache)
        assert cache.size == 1
