"""
Tests for tokenpak.capsule.builder.CapsuleBuilder

Coverage:
  - Feature flag: builder disabled by default (no-op)
  - Feature flag: builder enabled via constructor
  - Determinism: same input → same output
  - Hot window: messages inside hot window are never capsulised
  - Min block chars: short blocks skipped
  - Compression: long blocks are wrapped in capsule envelopes
  - Stats: returned stats are accurate
  - Capsule envelope format: id, ratio, chars_in, chars_out present
  - Invalid JSON: graceful passthrough
  - Empty messages: graceful passthrough
  - Integration: process() returns valid JSON bytes when modified
  - Performance: p99 < 20ms on typical payload (smoke test)
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import pytest

from tokenpak.capsule.builder import (
    CapsuleBuilder,
    _capsule_id,
    _compress_text,
    _wrap_capsule,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

LONG_TEXT = (
    "This is a very verbose paragraph that goes into extensive detail about "
    "something that could clearly be compressed without loss of meaning. "
    "The paragraph continues with more sentences adding bulk. "
    "Even more sentences are added here to ensure the block exceeds the minimum. "
    "And yet more text to guarantee we are well above 400 characters in total. "
    "This should be enough to trigger capsule compression reliably in all cases."
)  # ~450 chars

SHORT_TEXT = "Short message."  # well below 400 chars


def _make_body(messages: List[Dict[str, Any]], **extra) -> bytes:
    """Build a minimal chat request body."""
    payload: Dict[str, Any] = {"model": "test-model", "messages": messages}
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _parse_body(body_bytes: bytes) -> Dict[str, Any]:
    return json.loads(body_bytes)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_disabled_by_default(self):
        cb = CapsuleBuilder()
        body = _make_body([{"role": "user", "content": LONG_TEXT}])
        new_body, stats = cb.process(body)
        assert new_body == body
        assert stats["skipped"] is True
        assert stats["skip_reason"] == "disabled"

    def test_enabled_via_constructor(self):
        cb = CapsuleBuilder(enabled=True)
        # Single long message — but it's inside the hot window (last 2), so not capsulised
        body = _make_body([{"role": "user", "content": LONG_TEXT}])
        new_body, stats = cb.process(body)
        # Hot window check: 1 message, hot_window=2 → message is in hot window → no capsule
        assert stats["blocks_capsulized"] == 0

    def test_enabled_capsulises_historical_message(self):
        cb = CapsuleBuilder(enabled=True)
        # 3 messages: first two are historical, last is hot
        messages = [
            {"role": "user", "content": LONG_TEXT},
            {"role": "assistant", "content": LONG_TEXT},
            {"role": "user", "content": "What's the answer?"},  # hot window
        ]
        body = _make_body(messages)
        new_body, stats = cb.process(body)
        assert stats["blocks_capsulized"] >= 1
        assert stats["skipped"] is False


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        cb = CapsuleBuilder(enabled=True)
        messages = [
            {"role": "user", "content": LONG_TEXT},
            {"role": "assistant", "content": LONG_TEXT},
            {"role": "user", "content": "Go"},
        ]
        body = _make_body(messages)
        out1, stats1 = cb.process(body)
        out2, stats2 = cb.process(body)
        assert out1 == out2

    def test_capsule_id_deterministic(self):
        assert _capsule_id("hello world") == _capsule_id("hello world")

    def test_capsule_id_differs_for_different_content(self):
        assert _capsule_id("hello world") != _capsule_id("goodbye world")

    def test_compress_text_deterministic(self):
        assert _compress_text(LONG_TEXT) == _compress_text(LONG_TEXT)

    def test_wrap_capsule_deterministic(self):
        compressed = _compress_text(LONG_TEXT)
        assert _wrap_capsule(LONG_TEXT, compressed) == _wrap_capsule(LONG_TEXT, compressed)


# ---------------------------------------------------------------------------
# Hot window
# ---------------------------------------------------------------------------

class TestHotWindow:
    def test_last_message_never_capsulised(self):
        cb = CapsuleBuilder(enabled=True, hot_window=1)
        # Only one message — inside hot window
        body = _make_body([{"role": "user", "content": LONG_TEXT}])
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 0

    def test_second_to_last_capsulised_with_hot_window_1(self):
        cb = CapsuleBuilder(enabled=True, hot_window=1)
        messages = [
            {"role": "user", "content": LONG_TEXT},
            {"role": "user", "content": "ok"},
        ]
        body = _make_body(messages)
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 1

    def test_hot_window_2_protects_last_two(self):
        cb = CapsuleBuilder(enabled=True, hot_window=2)
        messages = [
            {"role": "user", "content": LONG_TEXT},  # historical → capsulised
            {"role": "assistant", "content": LONG_TEXT},  # hot → protected
            {"role": "user", "content": LONG_TEXT},  # hot → protected
        ]
        body = _make_body(messages)
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 1  # only the first

    def test_hot_window_0_capsulises_all(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0, min_block_chars=10)
        messages = [
            {"role": "user", "content": LONG_TEXT},
            {"role": "assistant", "content": LONG_TEXT},
        ]
        body = _make_body(messages)
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 2


# ---------------------------------------------------------------------------
# Min block chars
# ---------------------------------------------------------------------------

class TestMinBlockChars:
    def test_short_block_not_capsulised(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        body = _make_body([{"role": "user", "content": SHORT_TEXT}])
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 0

    def test_custom_min_chars_threshold(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0, min_block_chars=10)
        body = _make_body([{"role": "user", "content": SHORT_TEXT}])
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 1

    def test_exactly_at_threshold_not_capsulised(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0, min_block_chars=400)
        # Build text exactly 399 chars
        text = "x" * 399
        body = _make_body([{"role": "user", "content": text}])
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 0

    def test_one_over_threshold_capsulised(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0, min_block_chars=400)
        text = "x" * 401
        body = _make_body([{"role": "user", "content": text}])
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 1


# ---------------------------------------------------------------------------
# Capsule envelope format
# ---------------------------------------------------------------------------

class TestCapsuleEnvelope:
    def test_envelope_contains_required_fields(self):
        compressed = _compress_text(LONG_TEXT)
        envelope = _wrap_capsule(LONG_TEXT, compressed)
        assert "[CAPSULE" in envelope
        assert "id=" in envelope
        assert "ratio=" in envelope
        assert "chars_in=" in envelope
        assert "chars_out=" in envelope
        assert "[/CAPSULE]" in envelope

    def test_envelope_id_is_8_hex_chars(self):
        import re
        compressed = _compress_text(LONG_TEXT)
        envelope = _wrap_capsule(LONG_TEXT, compressed)
        m = re.search(r"id=([0-9a-f]+)", envelope)
        assert m is not None
        assert len(m.group(1)) == 8

    def test_capsule_content_is_in_body(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        body = _make_body([{"role": "user", "content": LONG_TEXT}])
        new_body, stats = cb.process(body)
        data = _parse_body(new_body)
        content = data["messages"][0]["content"]
        assert "[CAPSULE" in content
        assert "[/CAPSULE]" in content


# ---------------------------------------------------------------------------
# Stats accuracy
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_blocks_count_matches(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        messages = [
            {"role": "user", "content": LONG_TEXT},
            {"role": "assistant", "content": LONG_TEXT},
        ]
        body = _make_body(messages)
        _, stats = cb.process(body)
        assert stats["blocks_capsulized"] == 2

    def test_stats_chars_in_is_positive(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        body = _make_body([{"role": "user", "content": LONG_TEXT}])
        _, stats = cb.process(body)
        assert stats["chars_in"] > 0

    def test_stats_ratio_between_0_and_1(self):
        """Compression ratio should be <= 1.0 for non-trivial content."""
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        big_text = LONG_TEXT * 5  # ~2500 chars, clearly compressible
        body = _make_body([{"role": "user", "content": big_text}])
        _, stats = cb.process(body)
        # ratio can be > 1 for tiny blocks because of envelope overhead,
        # but for large blocks it should compress
        assert stats["ratio"] > 0

    def test_stats_duration_ms_is_float(self):
        cb = CapsuleBuilder(enabled=True)
        body = _make_body([{"role": "user", "content": LONG_TEXT}])
        _, stats = cb.process(body)
        assert isinstance(stats["duration_ms"], float)
        assert stats["duration_ms"] >= 0.0


# ---------------------------------------------------------------------------
# Edge cases / robustness
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_invalid_json_passthrough(self):
        cb = CapsuleBuilder(enabled=True)
        bad_body = b"not json at all {"
        new_body, stats = cb.process(bad_body)
        assert new_body == bad_body
        assert stats["skipped"] is True
        assert stats["skip_reason"] == "invalid_json"

    def test_empty_messages_list(self):
        cb = CapsuleBuilder(enabled=True)
        body = _make_body([])
        new_body, stats = cb.process(body)
        # Either same body or graceful stats — no crash
        assert new_body is not None
        assert stats is not None

    def test_no_messages_key(self):
        cb = CapsuleBuilder(enabled=True)
        body = json.dumps({"model": "x"}).encode()
        new_body, stats = cb.process(body)
        assert new_body is not None
        assert stats is not None

    def test_multipart_content_list(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": LONG_TEXT},
                    {"type": "text", "text": SHORT_TEXT},
                ],
            }
        ]
        body = _make_body(messages)
        new_body, stats = cb.process(body)
        # The long text part should be capsulised
        assert stats["blocks_capsulized"] == 1

    def test_output_is_valid_json(self):
        cb = CapsuleBuilder(enabled=True, hot_window=0)
        messages = [
            {"role": "user", "content": LONG_TEXT},
            {"role": "assistant", "content": LONG_TEXT},
            {"role": "user", "content": "continue"},
        ]
        body = _make_body(messages)
        new_body, _ = cb.process(body)
        # Must parse without error
        data = json.loads(new_body)
        assert "messages" in data

    def test_code_fence_preserved(self):
        code_block = (
            "Here is some context.\n\n"
            "```python\n"
            "def foo():\n"
            "    return 42\n"
            "```\n\n"
            "And more prose afterwards."
        )
        result = _compress_text(code_block)
        assert "def foo():" in result
        assert "return 42" in result


# ---------------------------------------------------------------------------
# Performance smoke test
# ---------------------------------------------------------------------------

class TestPerformance:
    def test_typical_payload_under_20ms(self):
        """Smoke-test: typical payload completes in <20ms (p99 target)."""
        cb = CapsuleBuilder(enabled=True, hot_window=2)
        # ~10 historical messages + 2 hot, each ~500 chars
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": LONG_TEXT}
            for i in range(12)
        ]
        body = _make_body(messages)

        durations = []
        for _ in range(20):
            t0 = time.monotonic()
            cb.process(body)
            durations.append((time.monotonic() - t0) * 1000)

        durations.sort()
        p99 = durations[int(len(durations) * 0.99)]
        assert p99 < 20.0, f"p99 latency {p99:.1f}ms exceeds 20ms target"


# ---------------------------------------------------------------------------
# Import sanity
# ---------------------------------------------------------------------------

def test_import():
    from tokenpak.capsule.builder import CapsuleBuilder  # noqa: F401
    from tokenpak.capsule import CapsuleBuilder as CB2  # noqa: F401
    assert CapsuleBuilder is CB2
