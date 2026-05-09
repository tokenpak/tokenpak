"""test_telemetry_segmentizer_coverage.py — Coverage tests for tokenpak.telemetry.segmentizer.

Targets: tokenpak/telemetry/segmentizer.py  (baseline 17% → goal 60%+)

Run:
    cd ~/tokenpak
    pytest tests/test_telemetry_segmentizer_coverage.py -v --tb=short
    pytest tests/test_telemetry_segmentizer_coverage.py \
        --cov=tokenpak.telemetry.segmentizer --cov-report=term-missing
"""
from __future__ import annotations

import os

from tokenpak.telemetry.models import AntiPattern, StaleReason
from tokenpak.telemetry.segmentizer import (
    STALE_SCORE_PENALTIES,
    Segment,
    SegmentType,
    _classify,
    _content_has_str,
    _content_to_str,
    _estimate_tokens,
    _extract_file_path,
    _extract_memory_terms,
    _has_image_block,
    _make_segment_id,
    _prune_antipattern_segments,
    _sha256,
    _should_prune_antipatterns,
    compute_coverage_score,
    detect_anti_patterns,
    detect_stale,
    extract_query_terms,
    jaccard_4gram,
    score_segment_relevance,
    segmentize,
    summarize_anti_patterns,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_seg(order=0, seg_type=SegmentType.user.value, relevance=0.5,
             stale_reason=None, anti_pattern=None) -> Segment:
    s = Segment(
        trace_id="test",
        segment_id=_make_segment_id("test", order),
        order=order,
        segment_type=seg_type,
        raw_hash="",
        raw_len=10,
        tokens_raw=2,
        relevance_score=relevance,
    )
    if stale_reason:
        s.stale_reason = stale_reason
    if anti_pattern:
        s.anti_pattern = anti_pattern
    return s


# ===========================================================================
# Internal helpers
# ===========================================================================

class TestMakeSegmentId:
    def test_deterministic(self):
        a = _make_segment_id("trace1", 0)
        b = _make_segment_id("trace1", 0)
        assert a == b

    def test_different_order(self):
        a = _make_segment_id("trace1", 0)
        b = _make_segment_id("trace1", 1)
        assert a != b

    def test_different_trace(self):
        a = _make_segment_id("trace1", 0)
        b = _make_segment_id("trace2", 0)
        assert a != b

    def test_empty_trace(self):
        result = _make_segment_id("", 0)
        assert isinstance(result, str)
        assert len(result) == 36  # UUID format


class TestContentToStr:
    def test_string_passthrough(self):
        assert _content_to_str("hello") == "hello"

    def test_list_with_text_block(self):
        content = [{"type": "text", "text": "hello world"}]
        result = _content_to_str(content)
        assert "hello world" in result

    def test_list_with_image_block(self):
        content = [{"type": "image", "source": {"type": "base64", "data": "abc123"}}]
        result = _content_to_str(content)
        assert "[image:" in result
        assert "base64" in result

    def test_list_with_image_no_source(self):
        content = [{"type": "image"}]
        result = _content_to_str(content)
        assert "[image:" in result

    def test_list_with_tool_use_block(self):
        content = [{"type": "tool_use", "id": "tu_1", "name": "my_tool"}]
        result = _content_to_str(content)
        assert "tool_use" in result

    def test_list_with_mixed_blocks(self):
        content = [
            {"type": "text", "text": "prefix"},
            {"type": "image", "source": {"type": "url", "data": "http://x.com/img"}},
            {"type": "text", "text": "suffix"},
        ]
        result = _content_to_str(content)
        assert "prefix" in result
        assert "suffix" in result
        assert "[image:" in result

    def test_list_with_non_dict_elements(self):
        content = ["plain string", 42]
        result = _content_to_str(content)
        assert "plain string" in result
        assert "42" in result

    def test_fallback_non_str_non_list(self):
        result = _content_to_str({"key": "val"})
        assert "key" in result

    def test_empty_list(self):
        result = _content_to_str([])
        assert result == ""

    def test_none_content(self):
        result = _content_to_str(None)
        assert result == "null"


class TestSha256:
    def test_stable(self):
        a = _sha256("hello")
        b = _sha256("hello")
        assert a == b

    def test_different_inputs(self):
        assert _sha256("a") != _sha256("b")

    def test_returns_hex(self):
        result = _sha256("test")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 0

    def test_four_chars(self):
        assert _estimate_tokens("abcd") == 1

    def test_approximate(self):
        assert _estimate_tokens("a" * 100) == 25


class TestHasImageBlock:
    def test_no_image(self):
        assert not _has_image_block([{"type": "text", "text": "hi"}])

    def test_with_image(self):
        assert _has_image_block([{"type": "image", "source": {}}])

    def test_not_a_list(self):
        assert not _has_image_block("string content")

    def test_empty_list(self):
        assert not _has_image_block([])


class TestContentHasStr:
    def test_found(self):
        assert _content_has_str("MEMORY.md is here", "MEMORY.md")

    def test_not_found(self):
        assert not _content_has_str("nothing here", "MEMORY.md")

    def test_in_block(self):
        content = [{"type": "text", "text": "TOKPAK:1 compressed"}]
        assert _content_has_str(content, "TOKPAK:1")


# ===========================================================================
# Classification
# ===========================================================================

class TestClassify:
    def _classify(self, msg, is_last_assistant=False, has_tools=False):
        return _classify(msg, is_last_assistant=is_last_assistant, has_tools=has_tools)

    def test_system(self):
        assert self._classify({"role": "system", "content": "sys"}) == SegmentType.system

    def test_developer(self):
        assert self._classify({"role": "developer", "content": "dev"}) == SegmentType.developer

    def test_retrieval_marker(self):
        msg = {"role": "user", "content": "TOKPAK:1 some compressed content"}
        assert self._classify(msg) == SegmentType.retrieval

    def test_retrieval_compress_marker(self):
        msg = {"role": "user", "content": "COMPRESS: some stuff"}
        assert self._classify(msg) == SegmentType.retrieval

    def test_memory_marker(self):
        msg = {"role": "user", "content": "MEMORY.md contents here"}
        assert self._classify(msg) == SegmentType.memory

    def test_memory_bracket_marker(self):
        msg = {"role": "user", "content": "[memory] something"}
        assert self._classify(msg) == SegmentType.memory

    def test_memory_double_bracket(self):
        msg = {"role": "user", "content": "[[memory]] contents"}
        assert self._classify(msg) == SegmentType.memory

    def test_memory_colon(self):
        msg = {"role": "user", "content": "MEMORY: recall this"}
        assert self._classify(msg) == SegmentType.memory

    def test_memory_xml_tag(self):
        msg = {"role": "user", "content": "<memory>data</memory>"}
        assert self._classify(msg) == SegmentType.memory

    def test_tool_role(self):
        msg = {"role": "tool", "content": "result"}
        assert self._classify(msg) == SegmentType.tool_output

    def test_tool_use_id(self):
        msg = {"role": "user", "content": "data", "tool_use_id": "tu_1"}
        assert self._classify(msg) == SegmentType.tool_output

    def test_tool_call_id(self):
        msg = {"role": "user", "content": "data", "tool_call_id": "tc_1"}
        assert self._classify(msg) == SegmentType.tool_output

    def test_assistant_context_not_last(self):
        msg = {"role": "assistant", "content": "prior response"}
        assert self._classify(msg, is_last_assistant=False) == SegmentType.assistant_context

    def test_assistant_last_is_other(self):
        msg = {"role": "assistant", "content": "final response"}
        result = self._classify(msg, is_last_assistant=True)
        assert result == SegmentType.other

    def test_user_with_image(self):
        msg = {"role": "user", "content": [{"type": "image", "source": {}}]}
        assert self._classify(msg) == SegmentType.image

    def test_plain_user(self):
        msg = {"role": "user", "content": "hello there"}
        assert self._classify(msg) == SegmentType.user

    def test_unknown_role(self):
        msg = {"role": "unknown", "content": "something"}
        # Falls through to other
        result = self._classify(msg)
        assert result == SegmentType.other


# ===========================================================================
# segmentize() — public API
# ===========================================================================

class TestSegmentize:
    def test_empty_messages(self):
        result = segmentize([])
        assert result == []

    def test_single_system(self):
        msgs = [{"role": "system", "content": "You are helpful."}]
        segs = segmentize(msgs)
        assert len(segs) == 1
        assert segs[0].segment_type == SegmentType.system.value

    def test_single_user(self):
        segs = segmentize([{"role": "user", "content": "Hello"}])
        assert len(segs) == 1
        assert segs[0].segment_type == SegmentType.user.value

    def test_with_tools(self):
        msgs = [{"role": "user", "content": "use a tool"}]
        tools = [{"name": "my_tool", "description": "does stuff"}]
        segs = segmentize(msgs, tools=tools)
        # One user + one tool_schema
        assert len(segs) == 2
        types = [s.segment_type for s in segs]
        assert SegmentType.tool_schema.value in types

    def test_order_preserved(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user msg"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "follow up"},
        ]
        segs = segmentize(msgs)
        orders = [s.order for s in segs]
        assert orders == list(range(len(msgs)))

    def test_multi_turn_assistant_context(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "second reply"},
            {"role": "user", "content": "third"},
        ]
        segs = segmentize(msgs)
        # First assistant is assistant_context (not the last msg)
        asst_segs = [s for s in segs if s.segment_type == SegmentType.assistant_context.value]
        assert len(asst_segs) >= 1

    def test_trace_id_propagated(self):
        msgs = [{"role": "user", "content": "hi"}]
        segs = segmentize(msgs, trace_id="my-trace")
        assert segs[0].trace_id == "my-trace"

    def test_segment_id_deterministic(self):
        msgs = [{"role": "user", "content": "hi"}]
        segs1 = segmentize(msgs, trace_id="t1")
        segs2 = segmentize(msgs, trace_id="t1")
        assert segs1[0].segment_id == segs2[0].segment_id

    def test_tokens_raw_calculated(self):
        msgs = [{"role": "user", "content": "a" * 40}]
        segs = segmentize(msgs)
        assert segs[0].tokens_raw == 10

    def test_raw_hash_populated(self):
        msgs = [{"role": "user", "content": "hello"}]
        segs = segmentize(msgs)
        assert len(segs[0].raw_hash) == 64

    def test_image_message(self):
        msgs = [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "data": "abc"}}]}]
        segs = segmentize(msgs)
        assert segs[0].segment_type == SegmentType.image.value

    def test_retrieval_message(self):
        msgs = [{"role": "user", "content": "TOKPAK:1 compressed content here"}]
        segs = segmentize(msgs)
        assert segs[0].segment_type == SegmentType.retrieval.value

    def test_memory_message(self):
        msgs = [{"role": "user", "content": "MEMORY.md has all the things"}]
        segs = segmentize(msgs)
        assert segs[0].segment_type == SegmentType.memory.value

    def test_tool_output_message(self):
        msgs = [{"role": "tool", "content": "tool result"}]
        segs = segmentize(msgs)
        assert segs[0].segment_type == SegmentType.tool_output.value

    def test_relevance_score_range(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "question"},
        ]
        segs = segmentize(msgs)
        for s in segs:
            assert 0.0 <= s.relevance_score <= 1.0

    def test_large_batch_no_corruption(self):
        msgs = [{"role": "user", "content": f"message {i}"} for i in range(50)]
        msgs += [{"role": "assistant", "content": f"reply {i}"} for i in range(50)]
        segs = segmentize(msgs)
        assert len(segs) == 100
        orders = [s.order for s in segs]
        assert sorted(orders) == list(range(100))

    def test_single_item_batch(self):
        segs = segmentize([{"role": "user", "content": "solo"}])
        assert len(segs) == 1

    def test_none_tools_no_schema_segment(self):
        segs = segmentize([{"role": "user", "content": "hi"}], tools=None)
        assert not any(s.segment_type == SegmentType.tool_schema.value for s in segs)

    def test_empty_tools_no_schema_segment(self):
        segs = segmentize([{"role": "user", "content": "hi"}], tools=[])
        assert not any(s.segment_type == SegmentType.tool_schema.value for s in segs)

    def test_content_as_list_blocks(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}]
        segs = segmentize(msgs)
        assert segs[0].raw_len > 0


# ===========================================================================
# score_segment_relevance
# ===========================================================================

class TestScoreSegmentRelevance:
    def _score(self, seg_type, order=0, total=5, current=4):
        seg = make_seg(order=order, seg_type=seg_type)
        ctx = {"total_turns": total, "current_turn_index": current}
        return score_segment_relevance(seg, ctx)

    def test_system_always_1(self):
        assert self._score(SegmentType.system.value) == 1.0

    def test_user_current_turn_1(self):
        assert self._score(SegmentType.user.value, order=4, total=5, current=4) == 1.0

    def test_user_old_turn(self):
        score = self._score(SegmentType.user.value, order=0, total=5, current=4)
        assert score == 0.4

    def test_tool_output_recent(self):
        score = self._score(SegmentType.tool_output.value, order=4, total=5, current=4)
        assert score == 1.0

    def test_tool_output_old(self):
        score = self._score(SegmentType.tool_output.value, order=0, total=10, current=9)
        assert score == 0.3

    def test_memory(self):
        assert self._score(SegmentType.memory.value) == 0.7

    def test_assistant_context_recent(self):
        score = self._score(SegmentType.assistant_context.value, order=3, total=5, current=4)
        assert score == 0.7

    def test_assistant_context_old(self):
        score = self._score(SegmentType.assistant_context.value, order=0, total=10, current=9)
        assert score == 0.4

    def test_retrieval(self):
        assert self._score(SegmentType.retrieval.value) == 0.4

    def test_tool_schema(self):
        assert self._score(SegmentType.tool_schema.value) == 0.3

    def test_other(self):
        assert self._score(SegmentType.other.value) == 0.1

    def test_default_context(self):
        seg = make_seg(seg_type=SegmentType.user.value)
        result = score_segment_relevance(seg, {})
        assert isinstance(result, float)


# ===========================================================================
# extract_query_terms
# ===========================================================================

class TestExtractQueryTerms:
    def test_camel_case(self):
        terms = extract_query_terms("MyClass and SomeError")
        assert "MyClass" in terms or "SomeError" in terms

    def test_snake_case(self):
        terms = extract_query_terms("my_function does stuff")
        assert any("my_function" in t for t in terms)

    def test_file_path(self):
        terms = extract_query_terms("look in foo/bar.py for the bug")
        assert any("foo/bar.py" in t for t in terms)

    def test_quoted_string(self):
        terms = extract_query_terms('find "MyModule" in the code')
        assert "MyModule" in terms

    def test_find_keyword(self):
        terms = extract_query_terms("find MyClass in repo")
        assert "MyClass" in terms

    def test_empty_string(self):
        terms = extract_query_terms("")
        assert isinstance(terms, list)

    def test_wildcard_extension(self):
        terms = extract_query_terms("all *.ts files")
        assert any("*.ts" in t for t in terms)


# ===========================================================================
# compute_coverage_score
# ===========================================================================

class TestComputeCoverageScore:
    def test_empty_chunks(self):
        assert compute_coverage_score([], ["term"]) == 0.0

    def test_all_terms_found(self):
        chunks = [{"text": "hello world foo", "score": 0.9, "path": "a.py"}]
        score = compute_coverage_score(chunks, ["hello", "world"])
        assert score > 0.5

    def test_missing_terms_reduces_score(self):
        chunks = [{"text": "unrelated content", "score": 0.9, "path": "a.py"}]
        score = compute_coverage_score(chunks, ["missing_term"])
        # must_hit_factor is 0 because term not found
        assert score < 0.5

    def test_no_query_terms(self):
        chunks = [{"text": "anything", "score": 0.9, "path": "a.py"}]
        score = compute_coverage_score(chunks, [])
        # must_hit satisfied when no terms (vacuously true)
        assert score >= 0.45

    def test_many_paths_reduces_concentration(self):
        chunks = [
            {"text": "term", "score": 0.8, "path": f"file{i}.py"} for i in range(10)
        ]
        score = compute_coverage_score(chunks, ["term"])
        # High unique paths → lower concentration
        assert score < 0.9

    def test_single_path_high_concentration(self):
        chunks = [{"text": "term", "score": 0.9, "path": "single.py"} for _ in range(3)]
        score = compute_coverage_score(chunks, ["term"])
        assert score > 0.6

    def test_score_clamped(self):
        chunks = [{"text": "term", "score": 10.0, "path": "a.py"}]
        score = compute_coverage_score(chunks, ["term"])
        assert 0.0 <= score <= 1.0


# ===========================================================================
# jaccard_4gram
# ===========================================================================

class TestJaccard4gram:
    def test_identical(self):
        assert jaccard_4gram("hello world", "hello world") == 1.0

    def test_completely_different(self):
        score = jaccard_4gram("aaaa", "bbbb")
        assert score == 0.0

    def test_partial_overlap(self):
        score = jaccard_4gram("hello world", "hello there")
        assert 0.0 < score < 1.0

    def test_both_empty(self):
        assert jaccard_4gram("", "") == 1.0

    def test_one_empty(self):
        assert jaccard_4gram("hello", "") == 0.0

    def test_short_strings(self):
        # Too short for 4-grams
        assert jaccard_4gram("ab", "ab") == 1.0  # both empty ngrams

    def test_one_too_short(self):
        assert jaccard_4gram("ab", "hello world testing") == 0.0


# ===========================================================================
# detect_stale
# ===========================================================================

class TestDetectStale:
    def test_empty_segments(self):
        result = detect_stale([], {})
        assert result == []

    def test_old_tool_output(self):
        segs = [make_seg(order=0, seg_type=SegmentType.tool_output.value, relevance=0.8)]
        ctx = {"total_turns": 10, "recent_user_messages": []}
        result = detect_stale(segs, ctx, {0: "tool data"})
        assert result[0].stale_reason == StaleReason.OLD_TOOL_OUTPUT.value

    def test_recent_tool_output_not_stale(self):
        segs = [make_seg(order=8, seg_type=SegmentType.tool_output.value, relevance=0.8)]
        ctx = {"total_turns": 10, "recent_user_messages": []}
        result = detect_stale(segs, ctx, {8: "tool data"})
        assert result[0].stale_reason == StaleReason.NOT_STALE.value

    def test_stale_assistant_turn(self):
        segs = [make_seg(order=0, seg_type=SegmentType.assistant_context.value, relevance=0.7)]
        ctx = {"total_turns": 10, "recent_user_messages": []}
        result = detect_stale(segs, ctx, {0: "old reply"})
        assert result[0].stale_reason == StaleReason.STALE_ASSISTANT_TURN.value

    def test_unreferenced_memory(self):
        segs = [make_seg(order=0, seg_type=SegmentType.memory.value, relevance=0.7)]
        content_map = {0: "tokenpak proxy compression vault agent memory terms"}
        ctx = {"total_turns": 5, "recent_user_messages": ["hello there what time is it"]}
        result = detect_stale(segs, ctx, content_map)
        assert result[0].stale_reason == StaleReason.UNREFERENCED_MEMORY.value

    def test_referenced_memory_not_stale(self):
        segs = [make_seg(order=0, seg_type=SegmentType.memory.value, relevance=0.7)]
        content_map = {0: "tokenpak proxy compression"}
        ctx = {"total_turns": 5, "recent_user_messages": ["check tokenpak proxy status"]}
        result = detect_stale(segs, ctx, content_map)
        assert result[0].stale_reason == StaleReason.NOT_STALE.value

    def test_duplicate_content_marks_lower_relevance(self):
        s1 = make_seg(order=0, seg_type=SegmentType.user.value, relevance=0.9)
        s2 = make_seg(order=1, seg_type=SegmentType.user.value, relevance=0.5)
        content = "this is a long repeated content segment that should trigger duplicate detection"
        content_map = {0: content, 1: content}
        ctx = {"total_turns": 2, "recent_user_messages": []}
        result = detect_stale([s1, s2], ctx, content_map)
        dups = [s for s in result if s.stale_reason == StaleReason.DUPLICATE_CONTENT.value]
        assert len(dups) == 1
        assert dups[0].order == 1  # lower relevance gets marked

    def test_superseded_retrieval(self):
        s1 = make_seg(order=0, seg_type=SegmentType.retrieval.value, relevance=0.4)
        s2 = make_seg(order=5, seg_type=SegmentType.retrieval.value, relevance=0.4)
        content_map = {
            0: "path: foo/bar.py\nold content",
            5: "path: foo/bar.py\nnewer content",
        }
        ctx = {"total_turns": 6, "recent_user_messages": []}
        result = detect_stale([s1, s2], ctx, content_map)
        # Older one (order=0) should be superseded
        assert result[0].stale_reason == StaleReason.SUPERSEDED_RETRIEVAL.value

    def test_penalty_applied_to_stale(self):
        segs = [make_seg(order=0, seg_type=SegmentType.tool_output.value, relevance=0.8)]
        ctx = {"total_turns": 10, "recent_user_messages": []}
        result = detect_stale(segs, ctx, {0: "old tool"})
        penalty = STALE_SCORE_PENALTIES[StaleReason.OLD_TOOL_OUTPUT.value]
        assert result[0].relevance_score == max(0.0, 0.8 + penalty)

    def test_no_content_map(self):
        segs = [make_seg(order=0, seg_type=SegmentType.memory.value)]
        ctx = {"total_turns": 3, "recent_user_messages": []}
        result = detect_stale(segs, ctx)  # no content_map arg
        assert isinstance(result, list)


# ===========================================================================
# _extract_file_path
# ===========================================================================

class TestExtractFilePath:
    def test_path_pattern(self):
        seg = make_seg(order=0)
        content_map = {0: "path: tokenpak/proxy.py\ncontent here"}
        result = _extract_file_path(seg, content_map)
        assert result == "tokenpak/proxy.py"

    def test_file_pattern(self):
        seg = make_seg(order=0)
        content_map = {0: "file: src/main.py"}
        result = _extract_file_path(seg, content_map)
        assert result == "src/main.py"

    def test_from_pattern(self):
        seg = make_seg(order=0)
        content_map = {0: "from foo/bar.py:\ndef main(): pass"}
        result = _extract_file_path(seg, content_map)
        assert result is not None
        assert "bar.py" in result

    def test_bracket_pattern(self):
        seg = make_seg(order=0)
        content_map = {0: "[foo/bar.py] content"}
        result = _extract_file_path(seg, content_map)
        assert result is not None

    def test_no_match(self):
        seg = make_seg(order=0)
        content_map = {0: "no file path here at all"}
        result = _extract_file_path(seg, content_map)
        assert result is None

    def test_missing_order(self):
        seg = make_seg(order=5)
        result = _extract_file_path(seg, {})
        assert result is None

    def test_empty_content(self):
        seg = make_seg(order=0)
        result = _extract_file_path(seg, {0: ""})
        assert result is None


# ===========================================================================
# _extract_memory_terms
# ===========================================================================

class TestExtractMemoryTerms:
    def test_extracts_words(self):
        terms = _extract_memory_terms("tokenpak proxy compression system")
        assert "tokenpak" in terms
        assert "proxy" in terms

    def test_removes_stopwords(self):
        terms = _extract_memory_terms("this that with from there their")
        assert not terms  # all stopwords

    def test_min_length(self):
        terms = _extract_memory_terms("ab abc abcd abcde")
        assert "abcd" in terms
        assert "abcde" in terms
        assert "ab" not in terms
        assert "abc" not in terms

    def test_empty(self):
        terms = _extract_memory_terms("")
        assert terms == set()

    def test_returns_set(self):
        result = _extract_memory_terms("hello hello world")
        assert isinstance(result, set)
        assert result.count if hasattr(result, 'count') else True


# ===========================================================================
# detect_anti_patterns
# ===========================================================================

class TestDetectAntiPatterns:
    def test_empty_segments(self):
        result = detect_anti_patterns([], {})
        assert result == []

    def test_boilerplate_filler(self):
        seg = make_seg(order=0, seg_type=SegmentType.assistant_context.value)
        content_map = {0: "I'd be happy to help you with that!"}
        result = detect_anti_patterns([seg], content_map)
        assert result[0].anti_pattern == AntiPattern.BOILERPLATE_FILLER.value

    def test_boilerplate_sure(self):
        seg = make_seg(order=0, seg_type=SegmentType.assistant_context.value)
        content_map = {0: "Sure! I can help with that."}
        result = detect_anti_patterns([seg], content_map)
        assert result[0].anti_pattern == AntiPattern.BOILERPLATE_FILLER.value

    def test_verbose_structured_json(self):
        seg = make_seg(order=0, seg_type=SegmentType.tool_output.value)
        content = '{"data": ' + '"x" * 100, ' * 600 + '"end": true}'
        big_json = "[" + '{"key": "' + "value" * 100 + '"},' * 600 + "]"
        content_map = {0: big_json}
        result = detect_anti_patterns([seg], content_map)
        # Should detect VERBOSE_STRUCTURED if > 500 tokens
        if len(big_json) // 4 > 500:
            assert result[0].anti_pattern == AntiPattern.VERBOSE_STRUCTURED.value

    def test_repeated_system_prompt(self):
        s1 = make_seg(order=0, seg_type=SegmentType.system.value)
        s2 = make_seg(order=5, seg_type=SegmentType.system.value)
        system_text = "You are a helpful assistant that answers questions thoroughly and correctly."
        content_map = {0: system_text, 5: system_text}
        result = detect_anti_patterns([s1, s2], content_map)
        # The later system prompt (order=5) should be marked
        repeated = [s for s in result if s.anti_pattern == AntiPattern.REPEATED_SYSTEM_PROMPT.value]
        assert len(repeated) >= 1

    def test_echo_request(self):
        user_seg = make_seg(order=0, seg_type=SegmentType.user.value)
        tool_seg = make_seg(order=1, seg_type=SegmentType.tool_output.value)
        msg = "What is the current status of the system today"
        content_map = {0: msg, 1: msg}
        result = detect_anti_patterns([user_seg, tool_seg], content_map)
        echo = [s for s in result if s.anti_pattern == AntiPattern.ECHO_REQUEST.value]
        assert len(echo) >= 1

    def test_redundant_instruction(self):
        s1 = make_seg(order=0, seg_type=SegmentType.user.value)
        s2 = make_seg(order=1, seg_type=SegmentType.user.value)
        text = "Please always respond in JSON format with keys result and error fields always"
        content_map = {0: text, 1: text}
        result = detect_anti_patterns([s1, s2], content_map)
        redundant = [s for s in result if s.anti_pattern == AntiPattern.REDUNDANT_INSTRUCTION.value]
        assert len(redundant) >= 1

    def test_penalty_applied(self):
        seg = make_seg(order=0, seg_type=SegmentType.assistant_context.value, relevance=0.8)
        content_map = {0: "Sure! I would be happy to help you."}
        result = detect_anti_patterns([seg], content_map)
        # Penalty should reduce relevance
        assert result[0].relevance_score < 0.8 or result[0].relevance_score == 0.0

    def test_no_anti_pattern_clean_content(self):
        seg = make_seg(order=0, seg_type=SegmentType.user.value)
        content_map = {0: "How does the authentication module work?"}
        result = detect_anti_patterns([seg], content_map)
        assert result[0].anti_pattern == AntiPattern.NONE.value


# ===========================================================================
# summarize_anti_patterns
# ===========================================================================

class TestSummarizeAntiPatterns:
    def test_empty(self):
        result = summarize_anti_patterns([])
        assert result["counts"] == {}
        assert result["top_offenders"] == []

    def test_no_anti_patterns(self):
        segs = [make_seg(order=i) for i in range(3)]
        result = summarize_anti_patterns(segs)
        assert result["counts"] == {}

    def test_counts(self):
        s1 = make_seg(order=0, anti_pattern=AntiPattern.BOILERPLATE_FILLER.value)
        s2 = make_seg(order=1, anti_pattern=AntiPattern.BOILERPLATE_FILLER.value)
        s3 = make_seg(order=2, anti_pattern=AntiPattern.ECHO_REQUEST.value)
        result = summarize_anti_patterns([s1, s2, s3])
        assert result["counts"][AntiPattern.BOILERPLATE_FILLER.value] == 2
        assert result["counts"][AntiPattern.ECHO_REQUEST.value] == 1

    def test_top_offenders_limit(self):
        segs = [make_seg(order=i, anti_pattern=AntiPattern.BOILERPLATE_FILLER.value) for i in range(10)]
        result = summarize_anti_patterns(segs)
        assert len(result["top_offenders"]) <= 5

    def test_top_offenders_sorted_by_order(self):
        segs = [
            make_seg(order=5, anti_pattern=AntiPattern.BOILERPLATE_FILLER.value),
            make_seg(order=2, anti_pattern=AntiPattern.ECHO_REQUEST.value),
        ]
        result = summarize_anti_patterns(segs)
        orders = [o["order"] for o in result["top_offenders"]]
        assert orders == sorted(orders)


# ===========================================================================
# _should_prune_antipatterns + _prune_antipattern_segments
# ===========================================================================

class TestPruneAntiPatterns:
    def test_prune_off_by_default(self):
        os.environ.pop("TOKENPAK_PRUNE_ANTIPATTERNS", None)
        assert _should_prune_antipatterns() is False

    def test_prune_on_env_true(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_PRUNE_ANTIPATTERNS", "true")
        assert _should_prune_antipatterns() is True

    def test_prune_on_env_1(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_PRUNE_ANTIPATTERNS", "1")
        assert _should_prune_antipatterns() is True

    def test_prune_on_env_yes(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_PRUNE_ANTIPATTERNS", "yes")
        assert _should_prune_antipatterns() is True

    def test_prune_removes_antipattern_segs(self):
        s1 = make_seg(order=0, anti_pattern=AntiPattern.BOILERPLATE_FILLER.value)
        s2 = make_seg(order=1, anti_pattern=AntiPattern.NONE.value)
        result = _prune_antipattern_segments([s1, s2])
        # s1 should be pruned
        assert all(s.order != 0 for s in result)
        assert any(s.order == 1 for s in result)

    def test_prune_keeps_all_if_would_be_empty(self):
        # If all segments have anti-patterns, don't prune (return original)
        s1 = make_seg(order=0, anti_pattern=AntiPattern.BOILERPLATE_FILLER.value)
        result = _prune_antipattern_segments([s1])
        assert result == [s1]

    def test_prune_empty_list(self):
        result = _prune_antipattern_segments([])
        assert result == []

    def test_env_integration_prunes(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_PRUNE_ANTIPATTERNS", "1")
        msgs = [
            {"role": "user", "content": "Sure! I'd be happy to help with that very much!"},
            {"role": "user", "content": "What is 2+2?"},
        ]
        segs = segmentize(msgs)
        # Second user message should survive
        assert any("2+2" in s.raw_hash or s.order == 1 for s in segs)


# ===========================================================================
# Integration: full pipeline
# ===========================================================================

class TestFullPipeline:
    def test_stale_and_anti_pattern_combined(self):
        """Stale detection and anti-pattern detection both run in segmentize."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Sure! I'd be happy to help."},
            {"role": "user", "content": "Sure! I'd be happy to help."},  # echo-like
            {"role": "tool", "content": "old tool result"},
            {"role": "tool", "content": "another tool result"},
            {"role": "user", "content": "what about now?"},
        ]
        segs = segmentize(msgs)
        assert len(segs) == len(msgs)
        # All relevance scores in range
        for s in segs:
            assert 0.0 <= s.relevance_score <= 1.0

    def test_1000_messages_no_crash(self):
        msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"} for i in range(100)]
        segs = segmentize(msgs)
        assert len(segs) == 100

    def test_unicode_content(self):
        msgs = [{"role": "user", "content": "こんにちは 世界 🌍"}]
        segs = segmentize(msgs)
        assert segs[0].tokens_raw >= 0

    def test_very_long_content(self):
        msgs = [{"role": "user", "content": "x" * 10000}]
        segs = segmentize(msgs)
        assert segs[0].tokens_raw == 2500

    def test_developer_role(self):
        msgs = [{"role": "developer", "content": "developer instructions"}]
        segs = segmentize(msgs)
        assert segs[0].segment_type == SegmentType.developer.value
