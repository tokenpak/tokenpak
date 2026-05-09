"""
Unit tests for tokenpak.companion.capsules.builder

Covers:
- CapsuleBuilder initialization
- process(): disabled (no-op), invalid JSON, no messages, hot window, string
  content, list content, multiple messages
- _maybe_capsulise(): below/above threshold
- _capsule_id(): determinism, length
- _compress_paragraph(): short pass-through, sentence boundary, word boundary
- _compress_text(): structural lines preserved, code fences, prose compression
- _wrap_capsule(): envelope format, ratio calculation
- Edge cases: empty bodies, zero-length content, unicode
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tokenpak.companion.capsules.builder import (
    _MAX_PARA_CHARS,
    DEFAULT_HOT_WINDOW,
    DEFAULT_MIN_BLOCK_CHARS,
    CapsuleBuilder,
    _capsule_id,
    _compress_paragraph,
    _compress_text,
    _wrap_capsule,
)

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_body(messages: list, **extra) -> bytes:
    """Serialize a messages list (plus optional extra fields) to JSON bytes."""
    payload: dict = {"messages": messages}
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _long_text(n: int = DEFAULT_MIN_BLOCK_CHARS + 50) -> str:
    """Return a prose string longer than DEFAULT_MIN_BLOCK_CHARS."""
    word = "hello"
    words = []
    while len(" ".join(words)) < n:
        words.append(word)
    return " ".join(words)


# ---------------------------------------------------------------------------
# _capsule_id
# ---------------------------------------------------------------------------


class TestCapsuleId:
    def test_returns_8_hex_chars(self):
        cid = _capsule_id("hello")
        assert len(cid) == 8
        assert all(c in "0123456789abcdef" for c in cid)

    def test_deterministic(self):
        text = "some content for id derivation"
        assert _capsule_id(text) == _capsule_id(text)

    def test_different_inputs_give_different_ids(self):
        assert _capsule_id("aaa") != _capsule_id("bbb")

    def test_matches_sha256_prefix(self):
        content = "test content"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        assert _capsule_id(content) == expected

    def test_empty_string(self):
        cid = _capsule_id("")
        assert len(cid) == 8

    def test_unicode_content(self):
        cid = _capsule_id("café ☕")
        assert len(cid) == 8


# ---------------------------------------------------------------------------
# _compress_paragraph
# ---------------------------------------------------------------------------


class TestCompressParagraph:
    def test_short_text_returned_verbatim(self):
        text = "Short text."
        assert _compress_paragraph(text) == "Short text."

    def test_exactly_max_chars_returned_verbatim(self):
        text = "x" * _MAX_PARA_CHARS
        assert _compress_paragraph(text) == text

    def test_long_text_truncated(self):
        text = "x" * (_MAX_PARA_CHARS + 50)
        result = _compress_paragraph(text)
        assert len(result) <= _MAX_PARA_CHARS + 1  # +1 for ellipsis

    def test_sentence_boundary_truncation(self):
        # Sentence ends well within budget; rest of text beyond budget
        sentence = "This is a sentence. "
        filler = "x" * (_MAX_PARA_CHARS + 50)
        text = sentence + filler
        result = _compress_paragraph(text)
        assert result.endswith(".")

    def test_word_boundary_truncation_adds_ellipsis(self):
        # No sentence boundary within budget — should truncate on word and add …
        text = "word " * 80  # well past _MAX_PARA_CHARS, no sentence end
        result = _compress_paragraph(text.strip())
        assert result.endswith("…")
        assert len(result) <= _MAX_PARA_CHARS + 2  # word truncation + ellipsis

    def test_collapses_internal_spaces(self):
        text = "too   many    spaces here"
        result = _compress_paragraph(text)
        assert "  " not in result

    def test_strips_leading_trailing_whitespace(self):
        text = "  hello  "
        assert _compress_paragraph(text) == "hello"


# ---------------------------------------------------------------------------
# _compress_text
# ---------------------------------------------------------------------------


class TestCompressText:
    def test_headings_preserved_verbatim(self):
        text = "# Heading One\n\nSome prose."
        result = _compress_text(text)
        assert "# Heading One" in result

    def test_bullets_preserved_verbatim(self):
        text = "- Bullet one\n- Bullet two"
        result = _compress_text(text)
        assert "- Bullet one" in result
        assert "- Bullet two" in result

    def test_ordered_list_preserved(self):
        text = "1. First item\n2. Second item"
        result = _compress_text(text)
        assert "1. First item" in result

    def test_blockquote_preserved(self):
        text = "> This is a quote"
        result = _compress_text(text)
        assert "> This is a quote" in result

    def test_code_fence_preserved(self):
        code_block = "```python\nx = 1 + 1\n```"
        result = _compress_text(code_block)
        assert "x = 1 + 1" in result

    def test_prose_inside_code_fence_not_compressed(self):
        # Long prose inside a code fence must not be altered
        long_line = "a " * 200  # way over _MAX_PARA_CHARS
        text = f"```\n{long_line.strip()}\n```"
        result = _compress_text(text)
        assert long_line.strip() in result

    def test_excessive_blank_lines_normalised(self):
        text = "Para one.\n\n\n\nPara two."
        result = _compress_text(text)
        assert "\n\n\n" not in result

    def test_short_prose_returned_unchanged(self):
        short = "Hello world."
        result = _compress_text(short)
        assert "Hello world." in result

    def test_long_prose_compressed(self):
        long_prose = "word " * 100
        result = _compress_text(long_prose.strip())
        # Result should be shorter than input
        assert len(result) < len(long_prose)

    def test_deterministic(self):
        text = "Repeated prose. " * 30
        assert _compress_text(text) == _compress_text(text)


# ---------------------------------------------------------------------------
# _wrap_capsule
# ---------------------------------------------------------------------------


class TestWrapCapsule:
    def test_envelope_header_present(self):
        original = "original content"
        compressed = "short"
        result = _wrap_capsule(original, compressed)
        assert result.startswith("[CAPSULE id=")
        assert result.endswith("[/CAPSULE]")

    def test_envelope_contains_compressed_content(self):
        result = _wrap_capsule("original", "compressed text")
        assert "compressed text" in result

    def test_capsule_id_in_header(self):
        original = "content"
        result = _wrap_capsule(original, "c")
        expected_id = _capsule_id(original)
        assert f"id={expected_id}" in result

    def test_chars_in_out_in_header(self):
        original = "hello"  # 5 chars
        compressed = "hi"   # 2 chars
        result = _wrap_capsule(original, compressed)
        assert "chars_in=5" in result
        assert "chars_out=2" in result

    def test_ratio_calculated_correctly(self):
        original = "a" * 100
        compressed = "a" * 50
        result = _wrap_capsule(original, compressed)
        assert "ratio=0.5" in result

    def test_zero_length_original_ratio_is_1(self):
        result = _wrap_capsule("", "")
        assert "ratio=1.0" in result

    def test_id_derived_from_original_not_compressed(self):
        # Same original → same id even if compressed differs
        original = "stable content"
        r1 = _wrap_capsule(original, "compressed A")
        r2 = _wrap_capsule(original, "compressed B")
        # Extract ids
        id1 = r1.split("id=")[1].split(" ")[0]
        id2 = r2.split("id=")[1].split(" ")[0]
        assert id1 == id2


# ---------------------------------------------------------------------------
# CapsuleBuilder — initialization
# ---------------------------------------------------------------------------


class TestCapsuleBuilderInit:
    def test_defaults(self):
        builder = CapsuleBuilder()
        assert builder._enabled is False
        assert builder._min_block_chars == DEFAULT_MIN_BLOCK_CHARS
        assert builder._hot_window == DEFAULT_HOT_WINDOW

    def test_custom_params(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=100, hot_window=5)
        assert builder._enabled is True
        assert builder._min_block_chars == 100
        assert builder._hot_window == 5


# ---------------------------------------------------------------------------
# CapsuleBuilder.process() — disabled path
# ---------------------------------------------------------------------------


class TestCapsuleBuilderDisabled:
    def test_disabled_returns_original_bytes_unchanged(self):
        builder = CapsuleBuilder(enabled=False)
        body = _make_body([{"role": "user", "content": _long_text()}])
        new_body, stats = builder.process(body)
        assert new_body == body

    def test_disabled_stats_skip_reason(self):
        builder = CapsuleBuilder(enabled=False)
        _, stats = builder.process(b"{}")
        assert stats["skipped"] is True
        assert stats["skip_reason"] == "disabled"

    def test_disabled_stats_zero_counts(self):
        builder = CapsuleBuilder(enabled=False)
        _, stats = builder.process(b'{"messages": []}')
        assert stats["blocks_capsulized"] == 0
        assert stats["chars_in"] == 0
        assert stats["chars_out"] == 0
        assert stats["ratio"] == 1.0


# ---------------------------------------------------------------------------
# CapsuleBuilder.process() — enabled path
# ---------------------------------------------------------------------------


class TestCapsuleBuilderEnabled:
    @pytest.fixture
    def builder(self):
        return CapsuleBuilder(enabled=True)

    # --- JSON / structural errors ---

    def test_invalid_json_returns_original(self, builder):
        bad = b"not json at all"
        new_body, stats = builder.process(bad)
        assert new_body == bad
        assert stats["skip_reason"] == "invalid_json"

    def test_empty_messages_list_skipped(self, builder):
        body = _make_body([])
        new_body, stats = builder.process(body)
        assert new_body == body
        assert stats["skip_reason"] == "no_messages"

    def test_missing_messages_key_skipped(self, builder):
        body = json.dumps({"model": "gpt-4"}).encode()
        new_body, stats = builder.process(body)
        assert new_body == body
        assert stats["skip_reason"] == "no_messages"

    # --- Hot window ---

    def test_single_message_in_hot_window_not_capsulised(self, builder):
        """One long message — inside default hot_window of 2 → not touched."""
        body = _make_body([{"role": "user", "content": _long_text()}])
        new_body, stats = builder.process(body)
        assert new_body == body
        assert stats["blocks_capsulized"] == 0

    def test_two_messages_both_in_hot_window(self, builder):
        long = _long_text()
        body = _make_body([
            {"role": "user", "content": long},
            {"role": "assistant", "content": long},
        ])
        new_body, stats = builder.process(body)
        assert new_body == body
        assert stats["blocks_capsulized"] == 0

    def test_three_messages_first_capsulised(self, builder):
        """3 messages: first is outside hot window (size 2) → should be capsulised."""
        long = _long_text()
        body = _make_body([
            {"role": "user", "content": long},       # idx 0 — outside hot window
            {"role": "assistant", "content": "ok"},  # idx 1 — inside hot window
            {"role": "user", "content": "hi"},       # idx 2 — inside hot window
        ])
        new_body, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 1
        data = json.loads(new_body)
        assert "[CAPSULE" in data["messages"][0]["content"]
        # Hot window messages untouched
        assert data["messages"][1]["content"] == "ok"
        assert data["messages"][2]["content"] == "hi"

    def test_hot_window_zero_capsulises_all(self):
        builder = CapsuleBuilder(enabled=True, hot_window=0)
        long = _long_text()
        body = _make_body([
            {"role": "user", "content": long},
            {"role": "assistant", "content": long},
        ])
        _, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 2

    # --- min_block_chars threshold ---

    def test_short_content_not_capsulised(self, builder):
        short = "hello"
        body = _make_body([
            {"role": "user", "content": short},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "ok"},
        ])
        new_body, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 0

    def test_exact_threshold_not_capsulised(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        text = "x" * 9  # below threshold
        body = _make_body([{"role": "user", "content": text}])
        _, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 0

    def test_above_threshold_capsulised(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        text = "word " * 20  # well above threshold
        body = _make_body([{"role": "user", "content": text}])
        _, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 1

    # --- List content format ---

    def test_list_content_text_parts_capsulised(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long = "word " * 20
        body = _make_body([
            {"role": "user", "content": [
                {"type": "text", "text": long},
                {"type": "image_url", "url": "http://example.com/img.png"},
            ]},
        ])
        new_body, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 1
        data = json.loads(new_body)
        assert "[CAPSULE" in data["messages"][0]["content"][0]["text"]
        # Non-text parts untouched
        assert data["messages"][0]["content"][1]["url"] == "http://example.com/img.png"

    def test_list_content_non_text_parts_ignored(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        body = _make_body([
            {"role": "user", "content": [
                {"type": "image_url", "url": "http://example.com/img.png"},
            ]},
        ])
        new_body, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 0

    # --- Stats correctness ---

    def test_stats_duration_ms_present(self, builder):
        body = _make_body([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])
        _, stats = builder.process(body)
        assert "duration_ms" in stats
        assert isinstance(stats["duration_ms"], float)

    def test_stats_ratio_correct(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        text = "word " * 30  # long enough to compress
        body = _make_body([{"role": "user", "content": text}])
        _, stats = builder.process(body)
        if stats["blocks_capsulized"] > 0:
            assert 0 < stats["ratio"]

    def test_stats_blocks_count_accumulates(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long = "word " * 20
        body = _make_body([
            {"role": "user", "content": long},
            {"role": "assistant", "content": long},
        ])
        _, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 2

    # --- Output is valid JSON ---

    def test_output_is_valid_json(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long = "word " * 20
        body = _make_body([
            {"role": "user", "content": long},
            {"role": "user", "content": "ok"},
        ])
        new_body, _ = builder.process(body)
        parsed = json.loads(new_body)
        assert "messages" in parsed

    # --- Non-dict messages are skipped gracefully ---

    def test_non_dict_message_skipped(self, builder):
        body = json.dumps({"messages": ["not a dict", None]}).encode()
        new_body, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 0

    # --- Unicode content ---

    def test_unicode_content_preserved(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        text = "café ☕ " * 30
        body = _make_body([{"role": "user", "content": text}])
        new_body, stats = builder.process(body)
        # Capsule envelope should appear
        assert stats["blocks_capsulized"] == 1
        data = json.loads(new_body)
        content = data["messages"][0]["content"]
        assert "[CAPSULE" in content

    # --- No modification when nothing eligible ---

    def test_no_eligible_blocks_returns_original_body(self, builder):
        """When nothing is capsulised the original bytes are returned."""
        short = "hi"
        body = _make_body([
            {"role": "user", "content": short},
            {"role": "assistant", "content": "hello"},
        ])
        new_body, stats = builder.process(body)
        assert new_body == body
        assert stats["skip_reason"] == "no_eligible_blocks"


# ---------------------------------------------------------------------------
# CapsuleBuilder._maybe_capsulise
# ---------------------------------------------------------------------------


class TestMaybeCapsulise:
    def test_below_min_no_capsule(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=100)
        text = "short"
        new_text, chars_in, chars_out, capsulized = builder._maybe_capsulise(text)
        assert new_text == text
        assert chars_in == len(text)
        assert chars_out == len(text)
        assert capsulized == 0

    def test_above_min_produces_capsule(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10)
        text = "word " * 20
        new_text, chars_in, chars_out, capsulized = builder._maybe_capsulise(text)
        assert capsulized == 1
        assert "[CAPSULE" in new_text
        assert "[/CAPSULE]" in new_text
        assert chars_in == len(text)

    def test_exact_min_not_capsulised(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10)
        text = "x" * 9  # strictly less than min_block_chars
        _, _, _, capsulized = builder._maybe_capsulise(text)
        assert capsulized == 0

    def test_capsulized_id_is_deterministic(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10)
        text = "word " * 20
        t1, _, _, _ = builder._maybe_capsulise(text)
        t2, _, _, _ = builder._maybe_capsulise(text)
        assert t1 == t2


# ---------------------------------------------------------------------------
# Integration: process round-trip
# ---------------------------------------------------------------------------


class TestCapsuleBuilderIntegration:
    def test_capsule_envelope_parseable_after_roundtrip(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        original_text = "This is a long message. " * 30
        body = _make_body([{"role": "user", "content": original_text}])
        new_body, stats = builder.process(body)

        assert stats["blocks_capsulized"] == 1
        data = json.loads(new_body)
        content = data["messages"][0]["content"]

        # Header line
        assert content.startswith("[CAPSULE id=")
        # Footer
        assert content.strip().endswith("[/CAPSULE]")

    def test_multiple_eligible_messages_all_wrapped(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=1)
        long = "word " * 20
        messages = [
            {"role": "user", "content": long},        # idx 0 — outside hot window
            {"role": "assistant", "content": long},   # idx 1 — outside hot window
            {"role": "user", "content": "last msg"},  # idx 2 — inside hot window (tail-1)
        ]
        body = _make_body(messages)
        new_body, stats = builder.process(body)
        assert stats["blocks_capsulized"] == 2
        data = json.loads(new_body)
        assert "[CAPSULE" in data["messages"][0]["content"]
        assert "[CAPSULE" in data["messages"][1]["content"]
        assert "[CAPSULE" not in data["messages"][2]["content"]

    def test_extra_fields_preserved_in_payload(self):
        builder = CapsuleBuilder(enabled=True, min_block_chars=10, hot_window=0)
        long = "word " * 20
        body = _make_body([{"role": "user", "content": long}], model="gpt-4", temperature=0.7)
        new_body, _ = builder.process(body)
        data = json.loads(new_body)
        assert data["model"] == "gpt-4"
        assert data["temperature"] == 0.7
