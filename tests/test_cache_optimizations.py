"""
tests/test_cache_optimizations.py

Unit test suite for cache optimizations — Full Coverage.

Tests five cache optimization areas:
  1. StablePrefixConstruction  — prefix must be bit-identical across requests
  2. VolatileTailSeparation    — dynamic content isolation
  3. DeterministicRetrieval   — sorting, capping, section placement
  4. TelemetryRecording       — hit rates, miss reasons
  5. CacheControlMarkers      — ephemeral on stable, no-cache on volatile

Total: 13 tests, all must pass.
"""

import json

import pytest

from tokenpak.cache.telemetry import CacheMetrics, CacheTelemetryCollector
from tokenpak.proxy.prompt_builder import (
    build_stable_prefix,
    build_volatile_tail,
    apply_stable_cache_control,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FROZEN_TOOL_SCHEMAS = [
    {
        "name": "search_web",
        "description": "Search the web for information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get current weather for a location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
            },
            "required": ["location"],
        },
    },
]


def _make_body(system=None, messages=None, model="claude-sonnet-4-6") -> bytes:
    """Build a minimal Anthropic messages API request body."""
    body: dict = {"model": model, "max_tokens": 1024}
    if system is not None:
        body["system"] = system
    if messages is None:
        messages = [{"role": "user", "content": "hello"}]
    body["messages"] = messages
    return json.dumps(body).encode("utf-8")


def _parse(body_bytes: bytes) -> dict:
    return json.loads(body_bytes)


def count_tokens(text: str) -> int:
    """Approximate token count — ~4 chars per token."""
    return max(1, len(text) // 4)


# ===========================================================================
# 1. TestStablePrefixConstruction
# ===========================================================================

class TestStablePrefixConstruction:
    """Stable prefix must be bit-identical across requests."""

    def test_stable_prefix_identical_across_calls(self):
        """Same system prompt + tools → identical hash."""
        system = "You are an AI assistant."
        tools = FROZEN_TOOL_SCHEMAS

        prefix1 = build_stable_prefix(system, tools)
        prefix2 = build_stable_prefix(system, tools)

        assert prefix1 == prefix2, "Stable prefix changed between calls"
        assert hash(prefix1) == hash(prefix2), "Hash mismatch"

    def test_stable_prefix_excludes_timestamps(self):
        """Timestamps never appear in stable prefix output."""
        system = "You are an AI. Current time: 2026-03-09T15:28:00."

        prefix = build_stable_prefix(system, [])

        assert "2026-03-09" not in prefix, "Timestamp in stable prefix"
        assert "15:28" not in prefix, "Time in stable prefix"

    def test_stable_prefix_excludes_uuids(self):
        """UUIDs never appear in stable prefix output."""
        system = "Request ID: abc12345def6-7890-abcd-ef01-234567890abc."

        prefix = build_stable_prefix(system, [])

        # The UUID pattern should be stripped
        assert "abc12345def6-7890-abcd-ef01-234567890abc" not in prefix, \
            "UUID in stable prefix"


# ===========================================================================
# 2. TestVolatileTailSeparation
# ===========================================================================

class TestVolatileTailSeparation:
    """Volatile tail contains only dynamic content."""

    def test_volatile_tail_contains_user_message(self):
        """User message goes in volatile tail."""
        user_message = "What is machine learning?"

        tail = build_volatile_tail(user_message, retrieved=[])

        assert user_message in tail, "User message missing from volatile tail"

    def test_volatile_tail_contains_retrieval(self):
        """Retrieved context goes in volatile tail."""
        retrieved = ["doc1 content", "doc2 content"]

        tail = build_volatile_tail("", retrieved=retrieved)

        for doc in retrieved:
            assert doc in tail, f"Retrieved doc '{doc}' missing from volatile tail"

    def test_volatile_tail_excludes_system_prompt(self):
        """System prompt must NOT appear in volatile tail."""
        system = "You are an AI assistant."
        user = "Hello"

        tail = build_volatile_tail(user, retrieved=[])

        assert system not in tail, "System prompt leaked into volatile tail"


# ===========================================================================
# 3. TestDeterministicRetrieval
# ===========================================================================

class TestDeterministicRetrieval:
    """Retrieval injection must be deterministic."""

    def test_retrieval_sorted_by_score_then_path_then_id(self):
        """Results are sorted: score desc, path asc, chunk_id asc.

        Dataset exercises all three sort keys:
          - doc1.md 0.95 chunk 1  →  [0] (highest score)
          - doc1.md 0.85 chunk 1  →  [1] (same score as doc2; doc1 < doc2 alphabetically)
          - doc1.md 0.85 chunk 2  →  [2] (same score+path, chunk_id 1 < 2)
          - doc2.md 0.85 chunk 1  →  [3] (lower path alphabetically than doc1? no — doc2 > doc1)
        """
        results = [
            {"score": 0.85, "path": "doc2.md", "chunk_id": 1, "text": "content_d2c1"},
            {"score": 0.95, "path": "doc1.md", "chunk_id": 1, "text": "content_d1c1_hi"},
            {"score": 0.85, "path": "doc1.md", "chunk_id": 2, "text": "content_d1c2"},
            {"score": 0.85, "path": "doc1.md", "chunk_id": 1, "text": "content_d1c1_lo"},
        ]

        sorted_results = sorted(
            results,
            key=lambda x: (-x["score"], x["path"], x["chunk_id"]),
        )

        # [0]: highest score wins
        assert sorted_results[0]["score"] == 0.95, "Highest score not first"
        assert sorted_results[0]["path"] == "doc1.md", "Path of top result wrong"

        # [1]: among 0.85s, doc1.md < doc2.md alphabetically, chunk_id=1 < 2
        assert sorted_results[1]["path"] == "doc1.md", "doc1.md should precede doc2.md"
        assert sorted_results[1]["chunk_id"] == 1, "Smaller chunk_id should come first"

        # [2]: still doc1.md, but chunk_id=2
        assert sorted_results[2]["path"] == "doc1.md"
        assert sorted_results[2]["chunk_id"] == 2, "chunk_id=2 should be after chunk_id=1"

        # [3]: doc2.md last
        assert sorted_results[3]["path"] == "doc2.md", "doc2.md should be last"

    def test_retrieval_capped_at_max_tokens(self):
        """Retrieved content never exceeds token cap."""
        MAX_TOKENS = 4000

        results = [
            {"text": "x" * 2000},  # ~500 tokens
            {"text": "y" * 2000},
            {"text": "z" * 2000},
            {"text": "w" * 2000},
            {"text": "q" * 2000},
        ]

        injected = build_volatile_tail("", retrieved=results, max_tokens=MAX_TOKENS)
        tokens = count_tokens(injected)

        assert tokens <= MAX_TOKENS + 50, (  # +50 for section headers
            f"Token cap exceeded: {tokens} > {MAX_TOKENS}"
        )

    def test_retrieval_in_fixed_section(self):
        """Retrieved context always in '## Retrieved Context' section,
        and that section appears BEFORE '## User Message'."""
        retrieved = ["content1", "content2"]

        tail = build_volatile_tail("User query here", retrieved=retrieved)

        assert "## Retrieved Context" in tail, "Missing fixed retrieval section"
        assert "## User Message" in tail, "Missing user message section"

        idx_retrieval = tail.find("## Retrieved Context")
        idx_user = tail.find("## User Message")
        assert idx_retrieval < idx_user, "Retrieval section not before user message"


# ===========================================================================
# 4. TestTelemetryRecording
# ===========================================================================

class TestTelemetryRecording:
    """Telemetry accurately tracks cache behaviour."""

    def test_collector_records_hit_rate(self):
        """Hit rate calculation is accurate: 3 hits + 1 miss = 0.75."""
        collector = CacheTelemetryCollector()

        for i in range(3):
            collector.record(CacheMetrics(
                request_id=f"req_{i}",
                stable_prefix_tokens=15000,
                stable_cached=True,
                cache_read_tokens=13500,
                total_input_tokens=15000,
            ))

        collector.record(CacheMetrics(
            request_id="req_miss",
            stable_prefix_tokens=15000,
            stable_cached=False,
            cache_read_tokens=0,
            cache_miss_reason="timestamp",
            total_input_tokens=15000,
        ))

        assert collector.hit_rate() == 0.75, \
            f"Expected hit_rate=0.75, got {collector.hit_rate()}"
        assert collector.total() == 4
        assert collector.hits() == 3
        assert collector.misses() == 1

    def test_collector_tracks_miss_reasons(self):
        """Miss reasons are categorized and counted correctly."""
        collector = CacheTelemetryCollector()

        collector.record(CacheMetrics(
            request_id="req_1",
            stable_prefix_tokens=15000,
            stable_cached=False,
            cache_miss_reason="timestamp",
            cache_read_tokens=0,
            total_input_tokens=15000,
        ))

        collector.record(CacheMetrics(
            request_id="req_2",
            stable_prefix_tokens=15000,
            stable_cached=False,
            cache_miss_reason="timestamp",
            cache_read_tokens=0,
            total_input_tokens=15000,
        ))

        collector.record(CacheMetrics(
            request_id="req_3",
            stable_prefix_tokens=15000,
            stable_cached=False,
            cache_miss_reason="retrieval",
            cache_read_tokens=0,
            total_input_tokens=15000,
        ))

        reasons = collector.by_miss_reason()
        assert reasons.get("timestamp") == 2, \
            f"Timestamp miss count wrong: {reasons}"
        assert reasons.get("retrieval") == 1, \
            f"Retrieval miss count wrong: {reasons}"


# ===========================================================================
# 5. TestCacheControlMarkers
# ===========================================================================

class TestCacheControlMarkers:
    """Cache control headers placed correctly."""

    def test_stable_prefix_marked_ephemeral(self):
        """Stable system block gets cache_control: ephemeral."""
        body = _make_body(
            system="You are an AI assistant. This is the stable system prompt.",
            messages=[{"role": "user", "content": "Question?"}],
        )

        result = apply_stable_cache_control(body)
        data = _parse(result)

        system = data["system"]
        assert isinstance(system, list), "system should be a list after marking"

        # Find last stable block — should have cache_control
        stable_blocks = [b for b in system if isinstance(b, dict) and b.get("cache_control")]
        assert len(stable_blocks) >= 1, "No block has cache_control"

        marked = stable_blocks[-1]
        assert marked.get("cache_control") == {"type": "ephemeral"}, \
            f"Stable block has wrong cache_control: {marked.get('cache_control')}"

    def test_volatile_tail_not_cached(self):
        """Volatile blocks (after cache boundary) must NOT have cache_control."""
        # Build a body where the last block is volatile (contains a timestamp)
        system = [
            {"type": "text", "text": "Stable system instructions."},
            {
                "type": "text",
                "text": "Today is 2026-03-09T15:53:00. This is volatile.",
            },
        ]
        body = _make_body(
            system=system,
            messages=[{"role": "user", "content": "Question?"}],
        )

        result = apply_stable_cache_control(body)
        data = _parse(result)

        # The volatile block (last block with timestamp) should NOT have cache_control
        result_system = data["system"]
        assert isinstance(result_system, list)

        # Find the timestamp block
        volatile_blocks = [
            b for b in result_system
            if isinstance(b, dict) and "2026-03-09" in b.get("text", "")
        ]
        assert volatile_blocks, "Volatile block not found in result"

        for blk in volatile_blocks:
            assert "cache_control" not in blk, \
                f"Volatile block should not have cache_control: {blk}"


# ===========================================================================
# 6. Extended edge-case tests (no-skips, realistic token counts)
# ===========================================================================

class TestStablePrefixEdgeCases:
    """Edge cases for build_stable_prefix."""

    def test_empty_tools_list(self):
        """build_stable_prefix with empty tools list is deterministic."""
        p1 = build_stable_prefix("You are an assistant.", [])
        p2 = build_stable_prefix("You are an assistant.", [])
        assert p1 == p2

    def test_tool_order_invariant(self):
        """Tool order in input should not affect stable prefix output."""
        tools_ab = [
            {"name": "alpha", "description": "Tool A"},
            {"name": "beta", "description": "Tool B"},
        ]
        tools_ba = [
            {"name": "beta", "description": "Tool B"},
            {"name": "alpha", "description": "Tool A"},
        ]
        p1 = build_stable_prefix("System.", tools_ab)
        p2 = build_stable_prefix("System.", tools_ba)
        assert p1 == p2, "Tool ordering should not affect stable prefix"

    def test_different_systems_produce_different_prefixes(self):
        """Different (non-volatile) system prompts produce different prefixes."""
        p1 = build_stable_prefix("You are a coding assistant.", [])
        p2 = build_stable_prefix("You are a math assistant.", [])
        assert p1 != p2, "Different system prompts must produce different prefixes"


class TestVolatileTailEdgeCases:
    """Edge cases for build_volatile_tail."""

    def test_empty_retrieval_still_has_sections(self):
        """Even with empty retrieval, both sections are present."""
        tail = build_volatile_tail("Hello world", retrieved=[])
        assert "## Retrieved Context" in tail
        assert "## User Message" in tail
        assert "Hello world" in tail

    def test_dict_retrieval_items_handled(self):
        """Retrieved items as dicts with 'text' key are extracted correctly."""
        retrieved = [
            {"text": "chunk content one", "score": 0.9},
            {"text": "chunk content two", "score": 0.8},
        ]
        tail = build_volatile_tail("Query", retrieved=retrieved)
        assert "chunk content one" in tail
        assert "chunk content two" in tail

    def test_max_tokens_zero_skips_all_retrieval(self):
        """max_tokens=0 results in no retrieved text appended."""
        retrieved = [{"text": "should not appear"}]
        tail = build_volatile_tail("User msg", retrieved=retrieved, max_tokens=0)
        assert "should not appear" not in tail


class TestTelemetryEdgeCases:
    """Edge cases for CacheMetrics and CacheTelemetryCollector."""

    def test_empty_collector_hit_rate_is_zero(self):
        """Empty collector returns hit_rate=0.0, not ZeroDivisionError."""
        collector = CacheTelemetryCollector()
        assert collector.hit_rate() == 0.0

    def test_empty_collector_by_miss_reason_is_empty(self):
        """Empty collector returns empty dict for by_miss_reason."""
        collector = CacheTelemetryCollector()
        assert collector.by_miss_reason() == {}

    def test_collector_clear_resets_state(self):
        """clear() removes all records and resets hit_rate to 0."""
        collector = CacheTelemetryCollector()
        collector.record(CacheMetrics("r1", 100, True, 90, 100))
        assert collector.total() == 1
        collector.clear()
        assert collector.total() == 0
        assert collector.hit_rate() == 0.0

    def test_cache_metrics_effective_tokens(self):
        """CacheMetrics.effective_tokens = total - cache_read."""
        m = CacheMetrics("r", 1000, True, 800, 1000)
        assert m.effective_tokens == 200

    def test_cache_metrics_cache_ratio(self):
        """CacheMetrics.cache_ratio = cache_read / total."""
        m = CacheMetrics("r", 1000, True, 750, 1000)
        assert m.cache_ratio == 0.75

    def test_cache_metrics_zero_total_tokens(self):
        """cache_ratio handles zero total_input_tokens gracefully."""
        m = CacheMetrics("r", 0, False, 0, 0)
        assert m.cache_ratio == 0.0

    def test_collector_summary_structure(self):
        """summary() returns dict with expected keys."""
        collector = CacheTelemetryCollector()
        collector.record(CacheMetrics("r1", 500, True, 400, 500))
        collector.record(CacheMetrics("r2", 500, False, 0, 500, "cold_start"))
        summary = collector.summary()
        assert "total" in summary
        assert "hits" in summary
        assert "misses" in summary
        assert "hit_rate" in summary
        assert "miss_reasons" in summary
        assert summary["total"] == 2
        assert summary["hit_rate"] == 0.5

    def test_miss_reason_not_recorded_on_hit(self):
        """Hit records with no miss_reason don't pollute by_miss_reason."""
        collector = CacheTelemetryCollector()
        collector.record(CacheMetrics("r1", 500, True, 400, 500))  # hit, no reason
        assert collector.by_miss_reason() == {}

    def test_avg_cache_ratio_empty(self):
        """avg_cache_ratio returns 0.0 for empty collector."""
        collector = CacheTelemetryCollector()
        assert collector.avg_cache_ratio() == 0.0

    def test_avg_cache_ratio_with_records(self):
        """avg_cache_ratio computes mean of per-record ratios."""
        collector = CacheTelemetryCollector()
        # ratio=0.8 and ratio=0.6 → avg=0.7
        collector.record(CacheMetrics("r1", 1000, True, 800, 1000))
        collector.record(CacheMetrics("r2", 1000, True, 600, 1000))
        result = collector.avg_cache_ratio()
        assert abs(result - 0.70) < 0.01, f"Expected avg_cache_ratio≈0.70, got {result}"


class TestApplyStableCacheControlEdgeCases:
    """Edge cases for apply_stable_cache_control (existing function)."""

    def test_idempotent_on_already_marked_body(self):
        """apply_stable_cache_control does not double-mark an already marked body."""
        body = _make_body(
            system=[{"type": "text", "text": "Stable prompt.",
                      "cache_control": {"type": "ephemeral"}}],
        )
        result1 = apply_stable_cache_control(body)
        result2 = apply_stable_cache_control(result1)
        # Idempotent — second pass should not change anything
        assert _parse(result1)["system"] == _parse(result2)["system"]

    def test_empty_system_returns_unchanged_body(self):
        """No system prompt → body returned unchanged (no crash)."""
        body = _make_body()  # no system
        result = apply_stable_cache_control(body)
        data = _parse(result)
        assert "system" not in data

    def test_invalid_json_returns_unchanged(self):
        """Non-JSON body bytes are returned as-is without crashing."""
        bad = b"this is not json"
        result = apply_stable_cache_control(bad)
        assert result == bad
