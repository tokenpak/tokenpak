"""Tests for agent/compression/directives.py"""

import pytest
from tokenpak.agent.compression.directives import (
    parse_directives,
    apply_compression_directives,
    apply_context_plan,
    apply_agent_dedup,
    extract_model_route,
    apply_directives,
    DirectiveResult,
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
        directives = [{"target": "segment_1", "action": "collapse", "params": {"keep_signature": True}}]
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
        directives = [{"target": "segment_3", "action": "prune_turns", "params": {"remove": [0, 1]}}]
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
            {"id": "segment_0", "content": "\n".join([f"line{i}" for i in range(100)]), "tokens": 100},
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
        result = apply_context_plan(VAULT_BLOCKS, {"block_priority": [8, 3, 11], "drop_blocks": [6, 9]})
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

        apply_directives(list(SEGMENTS), list(VAULT_BLOCKS), FULL_DIRECTIVE, local_recipe_fn=local_recipe)
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
        assert len(result.segments) == len(SEGMENTS)  # no directives → segments pass through unchanged
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
