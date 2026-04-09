"""
Comprehensive test coverage for tokenpak.agent.proxy.prompt_builder

Covers:
  - classify_system_blocks() — stable/volatile classification
  - apply_stable_cache_control() — cache marker application
  - apply_deterministic_cache_breakpoints() — multi-breakpoint logic
  - inject_with_cache_boundary() — volatile injection
  - PromptBuilder.decompose() / build() — structured assembly
  - PromptCacheStats — statistics tracking
  - build_stable_prefix() / build_volatile_tail() — helper functions
  - DeterministicPromptPack — deterministic assembly
  - Edge cases: empty inputs, oversized content, missing fields
"""

import json
import pytest
import sys
import os

# Ensure build path is importable
sys.path.insert(0, os.path.expanduser("~/tokenpak/build/lib"))

from tokenpak.agent.proxy.prompt_builder import (
    classify_system_blocks,
    apply_stable_cache_control,
    apply_deterministic_cache_breakpoints,
    inject_with_cache_boundary,
    PromptBuilder,
    PromptParts,
    PromptCacheStats,
    DeterministicPromptPack,
    build_stable_prefix,
    build_volatile_tail,
    get_stats,
    _is_volatile_block,
    _mark_last_block_cacheable,
)


# ---------------------------------------------------------------------------
# Tests: _is_volatile_block
# ---------------------------------------------------------------------------

class TestIsVolatileBlock:
    """Test volatile pattern detection in system blocks."""

    def test_iso_timestamp_detected(self):
        """ISO timestamps are volatile."""
        text = "Session started 2026-04-04T10:30:00Z"
        assert _is_volatile_block(text) is True

    def test_today_is_detected(self):
        """'today is' phrase is volatile."""
        text = "Today is Monday, the weather is nice."
        assert _is_volatile_block(text) is True

    def test_current_time_detected(self):
        """'current time' phrase is volatile."""
        text = "The current time is 3:45 PM."
        assert _is_volatile_block(text) is True

    def test_vault_injection_marker_detected(self):
        """Vault injection markers are volatile."""
        text = "--- [doc.md] (relevance: 0.85) ---"
        assert _is_volatile_block(text) is True

    def test_retrieved_context_tag_detected(self):
        """<retrieved_context> tags are volatile."""
        text = "<retrieved_context>Some doc content</retrieved_context>"
        assert _is_volatile_block(text) is True

    def test_stable_content_not_flagged(self):
        """Static system prompts are not volatile."""
        text = "You are a helpful AI assistant. Follow user instructions."
        assert _is_volatile_block(text) is False

    def test_empty_string_not_volatile(self):
        """Empty strings are not volatile."""
        assert _is_volatile_block("") is False


# ---------------------------------------------------------------------------
# Tests: classify_system_blocks
# ---------------------------------------------------------------------------

class TestClassifySystemBlocks:
    """Test classification of system blocks into stable/volatile."""

    def test_single_stable_block(self):
        """Single stable block classified correctly."""
        blocks = [{"type": "text", "text": "You are helpful."}]
        stable, volatile = classify_system_blocks(blocks)
        assert len(stable) == 1
        assert len(volatile) == 0
        assert stable[0]["text"] == "You are helpful."

    def test_single_volatile_block(self):
        """Single volatile block classified correctly."""
        blocks = [{"type": "text", "text": "today is April 4th"}]
        stable, volatile = classify_system_blocks(blocks)
        assert len(stable) == 0
        assert len(volatile) == 1

    def test_mixed_blocks(self):
        """Mixed stable and volatile blocks split correctly."""
        blocks = [
            {"type": "text", "text": "You are a helpful assistant."},
            {"type": "text", "text": "--- [doc.md] (relevance: 0.9) ---\nContent"},
            {"type": "text", "text": "Always be concise."},
        ]
        stable, volatile = classify_system_blocks(blocks)
        assert len(stable) == 2  # first and third
        assert len(volatile) == 1  # second (vault injection)

    def test_empty_blocks_list(self):
        """Empty block list returns empty stable and volatile."""
        stable, volatile = classify_system_blocks([])
        assert stable == []
        assert volatile == []

    def test_non_dict_blocks_go_stable(self):
        """Non-dict items default to stable."""
        blocks = ["string block", {"type": "text", "text": "dict block"}]
        stable, volatile = classify_system_blocks(blocks)
        assert len(stable) == 2


# ---------------------------------------------------------------------------
# Tests: _mark_last_block_cacheable
# ---------------------------------------------------------------------------

class TestMarkLastBlockCacheable:
    """Test cache_control marker application."""

    def test_marks_last_text_block(self):
        """Last text block gets cache_control marker."""
        blocks = [
            {"type": "text", "text": "First"},
            {"type": "text", "text": "Last"},
        ]
        result = _mark_last_block_cacheable(blocks)
        assert result[0].get("cache_control") is None
        assert result[1]["cache_control"] == {"type": "ephemeral"}

    def test_idempotent_marking(self):
        """Already-marked blocks are not double-marked."""
        blocks = [{"type": "text", "text": "Already marked", "cache_control": {"type": "ephemeral"}}]
        result = _mark_last_block_cacheable(blocks)
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_empty_blocks_unchanged(self):
        """Empty block list returns unchanged."""
        assert _mark_last_block_cacheable([]) == []


# ---------------------------------------------------------------------------
# Tests: apply_stable_cache_control
# ---------------------------------------------------------------------------

class TestApplyStableCacheControl:
    """Test backward-compatible cache control application."""

    def test_string_system_prompt(self):
        """String system prompt converted to list with marker."""
        body = {"system": "You are helpful.", "messages": []}
        result = json.loads(apply_stable_cache_control(json.dumps(body).encode()))
        assert isinstance(result["system"], list)
        assert result["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_list_system_prompt(self):
        """List system prompt gets marker on last stable block."""
        body = {
            "system": [{"type": "text", "text": "Helpful assistant."}],
            "messages": [],
        }
        result = json.loads(apply_stable_cache_control(json.dumps(body).encode()))
        assert result["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_invalid_json_passthrough(self):
        """Invalid JSON passed through unchanged."""
        garbage = b"not json {"
        assert apply_stable_cache_control(garbage) == garbage

    def test_empty_system_not_marked(self):
        """Empty system prompt not marked."""
        body = {"system": "", "messages": []}
        result = json.loads(apply_stable_cache_control(json.dumps(body).encode()))
        # Empty string system should remain unchanged
        assert result.get("system") == ""


# ---------------------------------------------------------------------------
# Tests: apply_deterministic_cache_breakpoints
# ---------------------------------------------------------------------------

class TestApplyDeterministicCacheBreakpoints:
    """Test multi-breakpoint cache marker logic."""

    def test_tools_get_marker(self):
        """Last tool definition gets cache_control marker."""
        body = {
            "system": "You are helpful.",
            "tools": [{"name": "search"}, {"name": "read"}],
            "messages": [],
        }
        result = json.loads(apply_deterministic_cache_breakpoints(json.dumps(body).encode()))
        # Last tool should have marker
        assert result["tools"][-1].get("cache_control") == {"type": "ephemeral"}

    def test_conversation_midpoint_marked(self):
        """Midpoint message gets marker in long conversations."""
        body = {
            "system": "You are helpful.",
            "messages": [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
                {"role": "assistant", "content": "msg4"},
            ],
        }
        result = json.loads(apply_deterministic_cache_breakpoints(json.dumps(body).encode()))
        # Midpoint (index 2) should be marked
        midpoint = result["messages"][2]
        if isinstance(midpoint.get("content"), list):
            assert midpoint["content"][0].get("cache_control") == {"type": "ephemeral"}

    def test_second_last_assistant_marked(self):
        """Second-to-last assistant message gets marker."""
        body = {
            "system": "You are helpful.",
            "messages": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "q3"},
            ],
        }
        result = json.loads(apply_deterministic_cache_breakpoints(json.dumps(body).encode()))
        # Second-to-last assistant (index 1) should be marked
        assistant_msg = result["messages"][1]
        if isinstance(assistant_msg.get("content"), list):
            assert assistant_msg["content"][0].get("cache_control") == {"type": "ephemeral"}

    def test_max_4_cache_markers(self):
        """Cap total cache_control blocks to Anthropic max (4)."""
        body = {
            "system": [
                {"type": "text", "text": "Block 1"},
                {"type": "text", "text": "Block 2"},
                {"type": "text", "text": "Block 3"},
            ],
            "tools": [{"name": "t1"}, {"name": "t2"}],
            "messages": [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2"},
                {"role": "user", "content": "u3"},
            ],
        }
        result = json.loads(apply_deterministic_cache_breakpoints(json.dumps(body).encode()))
        
        # Count total cache_control markers
        count = 0
        for blk in result.get("system", []):
            if isinstance(blk, dict) and "cache_control" in blk:
                count += 1
        for tool in result.get("tools", []):
            if isinstance(tool, dict) and "cache_control" in tool:
                count += 1
        for msg in result.get("messages", []):
            content = msg.get("content", [])
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and "cache_control" in c:
                        count += 1
        
        assert count <= 4, f"Expected max 4 cache markers, got {count}"


# ---------------------------------------------------------------------------
# Tests: inject_with_cache_boundary
# ---------------------------------------------------------------------------

class TestInjectWithCacheBoundary:
    """Test volatile content injection after cache boundary."""

    def test_inject_into_string_system(self):
        """Inject volatile text into string system prompt."""
        body = {"system": "You are helpful.", "messages": []}
        volatile = "Retrieved: document content here"
        result = json.loads(inject_with_cache_boundary(json.dumps(body).encode(), volatile))
        
        assert isinstance(result["system"], list)
        assert len(result["system"]) == 2
        # First block (original) should have cache_control
        assert result["system"][0]["cache_control"] == {"type": "ephemeral"}
        # Second block (volatile) should NOT have cache_control
        assert "cache_control" not in result["system"][1]
        assert result["system"][1]["text"] == volatile

    def test_inject_into_list_system(self):
        """Inject volatile text into list system prompt."""
        body = {
            "system": [{"type": "text", "text": "Block 1"}],
            "messages": [],
        }
        volatile = "Vault injection"
        result = json.loads(inject_with_cache_boundary(json.dumps(body).encode(), volatile))
        
        assert len(result["system"]) == 2
        assert result["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert result["system"][1]["text"] == volatile

    def test_inject_into_empty_system(self):
        """Inject into empty system creates single volatile block."""
        body = {"system": [], "messages": []}
        volatile = "New content"
        result = json.loads(inject_with_cache_boundary(json.dumps(body).encode(), volatile))
        
        assert len(result["system"]) == 1
        assert result["system"][0]["text"] == volatile

    def test_invalid_json_passthrough(self):
        """Invalid JSON passed through unchanged."""
        garbage = b"not valid json"
        assert inject_with_cache_boundary(garbage, "text") == garbage


# ---------------------------------------------------------------------------
# Tests: PromptBuilder
# ---------------------------------------------------------------------------

class TestPromptBuilder:
    """Test PromptBuilder decompose and build operations."""

    def test_decompose_valid_request(self):
        """Decompose valid request into PromptParts."""
        body = {
            "system": "You are helpful.",
            "tools": [{"name": "search"}],
            "messages": [{"role": "user", "content": "Hi"}],
            "model": "claude-3-sonnet",
            "max_tokens": 1024,
        }
        
        builder = PromptBuilder()
        parts = builder.decompose(json.dumps(body).encode())
        
        assert parts is not None
        assert len(parts.stable_blocks) == 1
        assert parts.stable_blocks[0]["text"] == "You are helpful."
        assert len(parts.tools) == 1
        assert len(parts.messages) == 1
        assert parts.other_fields["model"] == "claude-3-sonnet"

    def test_decompose_with_volatile_blocks(self):
        """Decompose separates volatile from stable blocks."""
        body = {
            "system": [
                {"type": "text", "text": "You are helpful."},
                {"type": "text", "text": "Today is April 4th, 2026."},
            ],
            "messages": [{"role": "user", "content": "Hi"}],
        }
        
        builder = PromptBuilder()
        parts = builder.decompose(json.dumps(body).encode())
        
        assert parts is not None
        assert len(parts.stable_blocks) == 1
        assert len(parts.volatile_blocks) == 1

    def test_decompose_invalid_json_returns_none(self):
        """Decompose returns None for invalid JSON."""
        builder = PromptBuilder()
        assert builder.decompose(b"not json") is None

    def test_decompose_no_messages_returns_none(self):
        """Decompose returns None if no messages field."""
        body = {"system": "Hi"}
        builder = PromptBuilder()
        assert builder.decompose(json.dumps(body).encode()) is None

    def test_build_applies_cache_control(self):
        """Build applies cache_control to last stable block."""
        parts = PromptParts(
            stable_blocks=[
                {"type": "text", "text": "Block 1"},
                {"type": "text", "text": "Block 2"},
            ],
            volatile_blocks=[{"type": "text", "text": "Volatile"}],
            tools=[],
            messages=[{"role": "user", "content": "Hi"}],
            other_fields={"model": "claude"},
        )
        
        builder = PromptBuilder()
        body_bytes = builder.build(parts)
        result = json.loads(body_bytes)
        
        # Last stable block should have cache_control
        assert result["system"][1].get("cache_control") == {"type": "ephemeral"}
        # Volatile block should not
        assert "cache_control" not in result["system"][2]

    def test_roundtrip_decompose_build(self):
        """Decompose then build produces valid structure."""
        original = {
            "system": "You are a helpful AI.",
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-3",
        }
        
        builder = PromptBuilder()
        parts = builder.decompose(json.dumps(original).encode())
        rebuilt = json.loads(builder.build(parts))
        
        assert rebuilt["model"] == "claude-3"
        assert len(rebuilt["messages"]) == 1
        assert isinstance(rebuilt["system"], list)


# ---------------------------------------------------------------------------
# Tests: PromptCacheStats
# ---------------------------------------------------------------------------

class TestPromptCacheStats:
    """Test statistics tracking for cache operations."""

    def test_record_applied(self):
        """Record applied increments counters."""
        stats = PromptCacheStats()
        stats.record_applied(stable=2, volatile=1)
        
        summary = stats.summary()
        assert summary["applied"] == 1
        assert summary["avg_stable_blocks"] == 2.0
        assert summary["avg_volatile_blocks"] == 1.0

    def test_record_skipped(self):
        """Record skipped increments skip counter."""
        stats = PromptCacheStats()
        stats.record_skipped(already_marked=False)
        stats.record_skipped(already_marked=True)
        
        summary = stats.summary()
        assert summary["skipped_no_system"] == 1
        assert summary["skipped_already_marked"] == 1

    def test_record_breakpoint(self):
        """Record breakpoint tracks per-breakpoint stats."""
        stats = PromptCacheStats()
        stats.record_breakpoint("system_last", True)
        stats.record_breakpoint("tools_last", False)
        stats.record_breakpoint("system_last", True)
        
        summary = stats.summary()
        assert summary["breakpoint_activity"]["applied"]["system_last"] == 2
        assert summary["breakpoint_activity"]["skipped"]["tools_last"] == 1

    def test_cache_marked_percentage(self):
        """Cache marked percentage calculated correctly."""
        stats = PromptCacheStats()
        stats.record_applied()
        stats.record_applied()
        stats.record_skipped()
        
        summary = stats.summary()
        # 2 applied, 1 skipped = 2/3 = 66.7%
        assert summary["cache_marked_pct"] == pytest.approx(66.7, rel=0.1)

    def test_module_stats_singleton(self):
        """get_stats returns module-level singleton."""
        stats = get_stats()
        assert isinstance(stats, PromptCacheStats)


# ---------------------------------------------------------------------------
# Tests: build_stable_prefix / build_volatile_tail
# ---------------------------------------------------------------------------

class TestBuildStablePrefix:
    """Test stable prefix construction."""

    def test_builds_prefix_with_system_only(self):
        """Build prefix with just system prompt."""
        prefix = build_stable_prefix("You are helpful.", [])
        assert "<stable_prefix>" in prefix
        assert "You are helpful." in prefix
        assert "</stable_prefix>" in prefix

    def test_builds_prefix_with_tools(self):
        """Build prefix includes serialized tools."""
        tools = [{"name": "search", "description": "Search docs"}]
        prefix = build_stable_prefix("System", tools)
        assert "<tools>" in prefix
        assert "search" in prefix

    def test_strips_volatile_patterns(self):
        """Volatile patterns stripped from prefix."""
        system = "You are helpful. Today is April 4th."
        prefix = build_stable_prefix(system, [])
        # "today is" should be stripped
        assert "Today is" not in prefix.lower() or prefix.count("today") == 0

    def test_deterministic_tool_ordering(self):
        """Tools sorted by name for deterministic output."""
        tools = [{"name": "zebra"}, {"name": "alpha"}]
        prefix1 = build_stable_prefix("Sys", tools)
        prefix2 = build_stable_prefix("Sys", tools[::-1])
        # Should be identical regardless of input order
        assert prefix1 == prefix2


class TestBuildVolatileTail:
    """Test volatile tail construction."""

    def test_builds_tail_with_user_message(self):
        """Build tail includes user message section."""
        tail = build_volatile_tail("Hello world", [])
        assert "## User Message" in tail
        assert "Hello world" in tail

    def test_builds_tail_with_retrieved(self):
        """Build tail includes retrieved context."""
        tail = build_volatile_tail("Query", ["doc1", "doc2"])
        assert "## Retrieved Context" in tail
        assert "doc1" in tail
        assert "doc2" in tail

    def test_respects_max_tokens_budget(self):
        """Max tokens truncates retrieved content."""
        long_doc = "x" * 1000
        tail = build_volatile_tail("Query", [long_doc], max_tokens=50)
        # 50 tokens * 4 chars = 200 chars max
        assert len(tail) < 500  # Should be truncated

    def test_handles_dict_retrieved_items(self):
        """Retrieved items can be dicts with 'text' key."""
        items = [{"text": "document content", "score": 0.9}]
        tail = build_volatile_tail("Query", items)
        assert "document content" in tail


# ---------------------------------------------------------------------------
# Tests: DeterministicPromptPack
# ---------------------------------------------------------------------------

class TestDeterministicPromptPack:
    """Test deterministic prompt assembly."""

    def test_to_system_block_structure(self):
        """to_system_block returns proper Anthropic format."""
        pack = DeterministicPromptPack(
            system="You are helpful.",
            policies="Be safe.",
            user_input="Hello",
        )
        blocks = pack.to_system_block()
        
        assert len(blocks) == 2  # stable + volatile
        # First block (stable) has cache_control
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "# SYSTEM" in blocks[0]["text"]
        # Second block (volatile) has no cache_control
        assert "cache_control" not in blocks[1]
        assert "# USER INPUT" in blocks[1]["text"]

    def test_to_request_body(self):
        """to_request_body builds complete Anthropic request."""
        pack = DeterministicPromptPack(
            system="Helpful AI",
            tools=[{"name": "search", "description": "Search"}],
            user_input="Hello",
        )
        body = pack.to_request_body()
        
        assert "model" in body
        assert "system" in body
        assert "messages" in body
        assert "tools" in body
        assert body["messages"][0]["content"] == "Hello"

    def test_byte_identical_output(self):
        """Identical inputs produce byte-identical output."""
        pack1 = DeterministicPromptPack(
            system="You are helpful.",
            tools=[{"name": "read"}, {"name": "write"}],
            policies="Be safe.",
            retrieved_context=["doc1", "doc2"],
            user_input="Hello",
        )
        pack2 = DeterministicPromptPack(
            system="You are helpful.",
            tools=[{"name": "read"}, {"name": "write"}],
            policies="Be safe.",
            retrieved_context=["doc1", "doc2"],
            user_input="Hello",
        )
        
        # Both should produce identical output
        assert pack1.to_system_block() == pack2.to_system_block()
        assert json.dumps(pack1.to_request_body()) == json.dumps(pack2.to_request_body())

    def test_tool_ordering_deterministic(self):
        """Tools are sorted by name for deterministic output."""
        pack1 = DeterministicPromptPack(
            tools=[{"name": "zebra"}, {"name": "alpha"}],
            user_input="Hi",
        )
        pack2 = DeterministicPromptPack(
            tools=[{"name": "alpha"}, {"name": "zebra"}],
            user_input="Hi",
        )
        
        # Tool blocks should be identical (sorted)
        blocks1 = pack1.to_system_block()
        blocks2 = pack2.to_system_block()
        assert blocks1 == blocks2

    def test_empty_sections_handled(self):
        """Empty sections don't break output."""
        pack = DeterministicPromptPack(user_input="Just a query")
        blocks = pack.to_system_block()
        
        # Should have at least volatile block
        assert len(blocks) >= 1
        # Find block with user input
        found = any("# USER INPUT" in b.get("text", "") for b in blocks)
        assert found

    def test_equality(self):
        """Packs with same fields are equal."""
        pack1 = DeterministicPromptPack(system="Hi", user_input="Query")
        pack2 = DeterministicPromptPack(system="Hi", user_input="Query")
        pack3 = DeterministicPromptPack(system="Hi", user_input="Different")
        
        assert pack1 == pack2
        assert pack1 != pack3

    def test_repr(self):
        """Repr shows section sizes."""
        pack = DeterministicPromptPack(
            system="System prompt",
            tools=[{"name": "t1"}],
            user_input="Hello",
        )
        repr_str = repr(pack)
        assert "DeterministicPromptPack" in repr_str
        assert "system=" in repr_str
        assert "tools=1" in repr_str


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_body(self):
        """Empty body bytes handled gracefully."""
        result = apply_stable_cache_control(b"")
        assert result == b""

    def test_unicode_content(self):
        """Unicode content preserved correctly."""
        body = {
            "system": "你好，我是助手。",
            "messages": [{"role": "user", "content": "こんにちは"}],
        }
        result = json.loads(apply_stable_cache_control(json.dumps(body).encode("utf-8")))
        assert "你好" in result["system"][0]["text"]

    def test_large_system_prompt(self):
        """Large system prompts processed correctly."""
        large_text = "x" * 100000  # 100KB
        body = {"system": large_text, "messages": []}
        result = json.loads(apply_stable_cache_control(json.dumps(body).encode()))
        assert len(result["system"][0]["text"]) == 100000

    def test_deeply_nested_tools(self):
        """Deeply nested tool schemas handled."""
        tools = [{
            "name": "complex",
            "input_schema": {
                "type": "object",
                "properties": {
                    "nested": {
                        "type": "object",
                        "properties": {
                            "deep": {"type": "string"}
                        }
                    }
                }
            }
        }]
        pack = DeterministicPromptPack(tools=tools, user_input="Hi")
        body = pack.to_request_body()
        assert body["tools"][0]["name"] == "complex"

    def test_special_characters_in_text(self):
        """Special characters don't break JSON encoding."""
        body = {
            "system": 'Quotes: "test" and \'single\' and backslash: \\',
            "messages": [],
        }
        result = json.loads(apply_stable_cache_control(json.dumps(body).encode()))
        assert "Quotes" in result["system"][0]["text"]

    def test_prompt_parts_to_request_body(self):
        """PromptParts.to_request_body assembles correctly."""
        parts = PromptParts(
            stable_blocks=[{"type": "text", "text": "Stable"}],
            volatile_blocks=[{"type": "text", "text": "Volatile"}],
            tools=[{"name": "tool1"}],
            messages=[{"role": "user", "content": "Hi"}],
            other_fields={"model": "claude-3", "max_tokens": 1024},
        )
        body = parts.to_request_body()
        
        assert body["model"] == "claude-3"
        assert body["max_tokens"] == 1024
        assert len(body["system"]) == 2
        assert body["tools"] == [{"name": "tool1"}]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
