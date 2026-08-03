"""
test_coverage_boost.py — Additional tests to boost coverage for langfuse-tokenpak.

Targets uncovered lines in:
- analytics.py: reset() method
- callback.py: on_chain_error, on_llm_start, on_llm_end, LlamaIndex callbacks
- tracer.py: various edge cases and branches

Task: TPK-COV-LANGFUSE-001
"""

import pytest
from langfuse_tokenpak.tracer import TokenPakTracer
from langfuse_tokenpak.callback import (
    TokenPakCallbackHandler,
    TokenPakLangChainCallback,
    TokenPakLlamaIndexCallback,
)
from langfuse_tokenpak.analytics import TokenPakAnalytics


# ==============================================================================
# Test Fixtures & Mock Classes
# ==============================================================================

class MockTrace:
    """Mock Langfuse trace object."""
    def __init__(self):
        self.updated = {}
        self.init_kwargs = {}
    
    def update(self, **kwargs):
        self.updated.update(kwargs)


class MockLangfuse:
    """Mock Langfuse client."""
    def __init__(self):
        self.traces = []
    
    def trace(self, **kwargs):
        t = MockTrace()
        t.init_kwargs = kwargs
        self.traces.append(t)
        return t
    
    def flush(self):
        pass


class FailingUpdateTrace:
    """Mock trace that fails on update()."""
    def update(self, **kwargs):
        raise RuntimeError("Update failed")


class FailingFlushLangfuse:
    """Mock Langfuse that fails on flush()."""
    def __init__(self):
        self.traces = []
    
    def trace(self, **kwargs):
        t = MockTrace()
        t.init_kwargs = kwargs
        self.traces.append(t)
        return t
    
    def flush(self):
        raise RuntimeError("Flush failed")


def make_block(btype="knowledge", tokens=100, bid="b1"):
    return {
        "id": bid,
        "type": btype,
        "tokens": tokens,
        "priority": "medium",
        "compacted": False,
    }


class FakePack:
    """Standard pack with .blocks and .budget."""
    def __init__(self, blocks, budget=8000):
        self.blocks = blocks
        self.budget = budget


class PackWithGetBlocks:
    """Pack with get_blocks() method instead of .blocks attribute."""
    def __init__(self, blocks, budget=None):
        self._blocks = blocks
        self.budget = budget
    
    def get_blocks(self):
        return self._blocks


class PackWithTokenBudget:
    """Pack with token_budget instead of budget."""
    def __init__(self, blocks, token_budget=4000):
        self.blocks = blocks
        self.token_budget = token_budget


class PackWithNoBudget:
    """Pack with no budget attribute at all."""
    def __init__(self, blocks):
        self.blocks = blocks


class PackWithNeitherBlocksNorGetBlocks:
    """Pack with neither .blocks nor .get_blocks() — empty extraction."""
    def __init__(self, budget=None):
        self.budget = budget


# ==============================================================================
# Analytics Tests
# ==============================================================================

class TestAnalyticsCoverageBoost:
    """Tests targeting analytics.py uncovered lines."""

    def test_reset_clears_all_state(self):
        """Test analytics.reset() clears all counters (lines 61-63)."""
        analytics = TokenPakAnalytics()
        
        # Record some data
        blocks = [make_block("knowledge", 400), make_block("memory", 200)]
        analytics.record_pack(blocks, budget=1000, raw_tokens=1000)
        
        report_before = analytics.get_report()
        assert report_before["pack_count"] == 1
        assert report_before["total_tokens_after"] == 600
        
        # Reset
        analytics.reset()
        
        report_after = analytics.get_report()
        assert report_after["pack_count"] == 0
        assert report_after["total_tokens_after"] == 0
        assert report_after["total_tokens_before"] == 0
        assert report_after["tokens_saved"] == 0
        assert report_after["top_blocks"] == []

    def test_analytics_with_block_objects(self):
        """Test analytics with actual objects (not dicts)."""
        class BlockObject:
            def __init__(self, btype, tokens, bid):
                self.type = btype
                self.tokens = tokens
                self.id = bid
        
        analytics = TokenPakAnalytics()
        blocks = [
            BlockObject("knowledge", 300, "kb1"),
            BlockObject("memory", 200, "mem1"),
        ]
        analytics.record_pack(blocks, budget=1000)
        
        report = analytics.get_report()
        assert report["pack_count"] == 1
        assert report["total_tokens_after"] == 500

    def test_analytics_zero_raw_tokens(self):
        """Test edge case where raw_tokens is 0."""
        analytics = TokenPakAnalytics()
        analytics.record_pack([], budget=1000, raw_tokens=0)
        
        report = analytics.get_report()
        assert report["savings_percent"] == 0.0
        assert report["compression_ratio"] == 1.0


# ==============================================================================
# Tracer Tests
# ==============================================================================

class TestTracerCoverageBoost:
    """Tests targeting tracer.py uncovered lines."""

    def test_extract_blocks_via_get_blocks(self):
        """Test _extract_blocks with pack.get_blocks() method (lines 76-78)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = PackWithGetBlocks([make_block("memory", 150)], budget=2000)
        
        with tracer.trace_pack(pack, name="get_blocks_test") as span:
            pass
        
        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta["total_tokens"] == 150

    def test_extract_budget_via_token_budget(self):
        """Test _extract_budget with pack.token_budget (line 85)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = PackWithTokenBudget([make_block("knowledge", 200)], token_budget=5000)
        
        with tracer.trace_pack(pack, name="token_budget_test") as span:
            pass
        
        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta["budget"] == 5000

    def test_extract_budget_none(self):
        """Test _extract_budget returns None when no budget attr."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = PackWithNoBudget([make_block("instructions", 100)])
        
        with tracer.trace_pack(pack, name="no_budget_test") as span:
            pass
        
        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta.get("budget") is None

    def test_extract_blocks_empty_fallback(self):
        """Test _extract_blocks returns [] when no .blocks or .get_blocks() (line 78)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = PackWithNeitherBlocksNorGetBlocks(budget=1000)
        
        with tracer.trace_pack(pack, name="empty_extraction_test") as span:
            pass
        
        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta["total_tokens"] == 0
        assert meta["block_count"] == 0

    def test_trace_blocks_disabled(self):
        """Test trace with trace_blocks=False (line 97)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf, trace_blocks=False)
        pack = FakePack([make_block("knowledge", 200)])
        
        with tracer.trace_pack(pack) as span:
            pass
        
        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert "blocks" not in meta

    def test_trace_compression_disabled(self):
        """Test trace with trace_compression=False (line 99)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf, trace_compression=False)
        pack = FakePack([make_block("knowledge", 200, "comp_test")])
        
        with tracer.trace_pack(pack) as span:
            pass
        
        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        # compacted_blocks should be removed
        assert "compacted_blocks" not in meta or meta.get("compacted_blocks") is None

    def test_trace_with_user_and_session_id(self):
        """Test trace_pack with user_id and session_id (lines 155, 157)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack([make_block("knowledge", 100)])
        
        with tracer.trace_pack(
            pack,
            name="auth_test",
            user_id="user_123",
            session_id="session_456",
        ) as span:
            pass
        
        trace_kwargs = lf.traces[0].init_kwargs
        assert trace_kwargs["user_id"] == "user_123"
        assert trace_kwargs["session_id"] == "session_456"

    def test_record_output_with_content_attr(self):
        """Test record_output with .content attribute (line 181)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack([make_block("instructions", 50)])
        
        class ResponseWithContent:
            content = "This is content."
        
        with tracer.trace_pack(pack) as span:
            tracer.record_output(span, ResponseWithContent())
        
        assert lf.traces[0].updated["output"] == "This is content."

    def test_record_output_with_choices_openai_style(self):
        """Test record_output with OpenAI-style .choices (lines 187, 190-193)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack([make_block("instructions", 50)])
        
        class MessageChoice:
            class Message:
                content = "OpenAI response content"
            message = Message()
        
        class OpenAIResponse:
            choices = [MessageChoice()]
        
        with tracer.trace_pack(pack) as span:
            tracer.record_output(span, OpenAIResponse())
        
        assert lf.traces[0].updated["output"] == "OpenAI response content"

    def test_record_output_with_broken_choices(self):
        """Test record_output falls back to str() for broken .choices."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack([make_block("instructions", 50)])
        
        class BrokenResponse:
            choices = []  # Empty choices will fail
            def __str__(self):
                return "BrokenResponse()"
        
        with tracer.trace_pack(pack) as span:
            tracer.record_output(span, BrokenResponse())
        
        assert "BrokenResponse" in str(lf.traces[0].updated.get("output", ""))

    def test_record_output_with_usage(self):
        """Test record_output with usage dict (line 198, 200-201)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack([make_block("instructions", 50)])
        
        with tracer.trace_pack(pack) as span:
            tracer.record_output(
                span,
                "Response text",
                usage={"prompt_tokens": 100, "completion_tokens": 50},
            )
        
        assert lf.traces[0].updated["output"] == "Response text"
        assert lf.traces[0].updated["usage"]["prompt_tokens"] == 100

    def test_record_output_update_fails_silently(self):
        """Test record_output handles trace.update() failure (lines 205-208)."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack([make_block("instructions", 50)])
        
        # Replace the trace with one that fails on update
        failing_trace = FailingUpdateTrace()
        
        # This should not raise
        tracer.record_output(failing_trace, "Response text")

    def test_record_output_none_trace(self):
        """Test record_output with None trace."""
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        
        # Should not raise, just return
        tracer.record_output(None, "Response text")

    def test_flush_fails_silently(self):
        """Test flush() handles failure gracefully."""
        lf = FailingFlushLangfuse()
        tracer = TokenPakTracer(lf)
        
        # Should not raise
        tracer.flush()


# ==============================================================================
# Callback Tests
# ==============================================================================

class TestCallbackCoverageBoost:
    """Tests targeting callback.py uncovered lines."""

    def test_langfuse_trace_failure_silent(self):
        """Test TokenPakCallbackHandler handles Langfuse trace failure."""
        class BrokenLangfuse:
            def trace(self, **kwargs):
                raise RuntimeError("Langfuse down")
            def flush(self):
                pass
        
        handler = TokenPakCallbackHandler(BrokenLangfuse())
        pack = FakePack([make_block("knowledge", 100)])
        
        class FakeCompiled:
            blocks = [make_block("knowledge", 100)]
            total_tokens = 100
            compression_ratio = 1.0
        
        # Should not raise
        handler.on_tokenpak_compile(pack, FakeCompiled())

    def test_langchain_on_chain_error(self):
        """Test TokenPakLangChainCallback.on_chain_error (lines 128-130)."""
        errors_received = []
        
        class MockHandler:
            def on_chain_error(self, error, **kwargs):
                errors_received.append(error)
        
        cb = TokenPakLangChainCallback(langfuse_handler=MockHandler())
        cb.on_tokenpak_pack(FakePack([make_block("test", 100)]))
        
        test_error = ValueError("Chain failed")
        cb.on_chain_error(test_error)
        
        assert len(errors_received) == 1
        assert errors_received[0] is test_error
        assert cb._current_pack_meta is None  # Cleared after error

    def test_langchain_on_chain_error_no_handler(self):
        """Test on_chain_error without langfuse_handler."""
        cb = TokenPakLangChainCallback()
        cb.on_tokenpak_pack(FakePack([make_block("test", 100)]))
        
        # Should not raise
        cb.on_chain_error(ValueError("No handler"))
        assert cb._current_pack_meta is None

    def test_langchain_on_chain_end_with_handler(self):
        """Test on_chain_end forwards to langfuse_handler (line 124)."""
        outputs_received = []
        
        class MockHandler:
            def on_chain_end(self, outputs, **kwargs):
                outputs_received.append(outputs)
        
        cb = TokenPakLangChainCallback(langfuse_handler=MockHandler())
        cb.on_tokenpak_pack(FakePack([make_block("test", 100)]))
        cb.on_chain_end({"result": "success"})
        
        assert len(outputs_received) == 1
        assert outputs_received[0]["result"] == "success"
        assert cb._current_pack_meta is None

    def test_langchain_on_llm_start(self):
        """Test TokenPakLangChainCallback.on_llm_start (lines 138-139)."""
        llm_starts = []
        
        class MockHandler:
            def on_llm_start(self, serialized, prompts, **kwargs):
                llm_starts.append((serialized, prompts))
        
        cb = TokenPakLangChainCallback(langfuse_handler=MockHandler())
        cb.on_llm_start({"model": "gpt-4"}, ["Hello, world!"])
        
        assert len(llm_starts) == 1
        assert llm_starts[0][1] == ["Hello, world!"]

    def test_langchain_on_llm_start_no_handler(self):
        """Test on_llm_start without langfuse_handler."""
        cb = TokenPakLangChainCallback()
        # Should not raise
        cb.on_llm_start({}, [])

    def test_langchain_on_llm_end(self):
        """Test TokenPakLangChainCallback.on_llm_end (lines 142-143)."""
        responses_received = []
        
        class MockHandler:
            def on_llm_end(self, response, **kwargs):
                responses_received.append(response)
        
        cb = TokenPakLangChainCallback(langfuse_handler=MockHandler())
        
        class FakeResponse:
            text = "LLM output"
        
        cb.on_llm_end(FakeResponse())
        
        assert len(responses_received) == 1

    def test_langchain_on_llm_end_no_handler(self):
        """Test on_llm_end without langfuse_handler."""
        cb = TokenPakLangChainCallback()
        # Should not raise
        cb.on_llm_end(None)

    def test_llamaindex_start_trace(self):
        """Test TokenPakLlamaIndexCallback.start_trace (line 216-217)."""
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf)
        
        # Should not raise
        cb.start_trace(trace_id="trace_123")

    def test_llamaindex_end_trace(self):
        """Test TokenPakLlamaIndexCallback.end_trace (lines 221, 229)."""
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf)
        
        # Should not raise
        cb.end_trace(trace_id="trace_123", trace_map={"key": "value"})

    def test_llamaindex_event_end_ignored(self):
        """Test on_event_end with ignored event type (line 204)."""
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf, event_ends_to_ignore=["embedding"])
        pack = FakePack([make_block("test", 100)])
        
        cb.on_event_start("embedding", payload={"tokenpak_pack": pack}, event_id="evt1")
        cb.on_event_end("embedding", event_id="evt1")
        
        # Should have no traces since end was ignored
        assert len(lf.traces) == 0

    def test_llamaindex_trace_failure_silent(self):
        """Test LlamaIndex callback handles Langfuse trace failure."""
        class BrokenLangfuse:
            def trace(self, **kwargs):
                raise RuntimeError("Langfuse down")
        
        cb = TokenPakLlamaIndexCallback(BrokenLangfuse())
        pack = FakePack([make_block("test", 100)])
        
        cb.on_event_start("query", payload={"tokenpak_pack": pack}, event_id="evt1")
        # Should not raise
        cb.on_event_end("query", event_id="evt1")

    def test_llamaindex_event_start_no_payload(self):
        """Test on_event_start with None payload."""
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf)
        
        result = cb.on_event_start("query", payload=None, event_id="evt1")
        assert result == "evt1"  # Should return event_id
        
        cb.on_event_end("query", event_id="evt1")
        assert len(lf.traces) == 0  # No pack captured


# ==============================================================================
# Visualization Tests (additional edge cases)
# ==============================================================================

class TestVisualizationCoverageBoost:
    """Additional tests for visualization.py edge cases."""

    def test_blocks_to_metadata_empty_list(self):
        """Test blocks_to_metadata with empty list."""
        from langfuse_tokenpak.visualization import blocks_to_metadata
        
        meta = blocks_to_metadata([])
        assert meta["total_tokens"] == 0
        assert meta["block_count"] == 0

    def test_ascii_block_summary_empty(self):
        """Test ascii_block_summary with empty list."""
        from langfuse_tokenpak.visualization import ascii_block_summary
        
        summary = ascii_block_summary([])
        assert "TokenPak Pack" in summary
        assert "0 blocks" in summary or "Total: 0" in summary
