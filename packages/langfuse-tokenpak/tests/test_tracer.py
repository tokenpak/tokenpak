"""Tests for langfuse_tokenpak.tracer — uses a mock Langfuse client."""

import pytest
from langfuse_tokenpak.tracer import TokenPakTracer
from langfuse_tokenpak.analytics import TokenPakAnalytics


class MockTrace:
    def __init__(self):
        self.updated = {}

    def update(self, **kwargs):
        self.updated.update(kwargs)


class MockLangfuse:
    def __init__(self):
        self.traces = []

    def trace(self, **kwargs):
        t = MockTrace()
        t.init_kwargs = kwargs
        self.traces.append(t)
        return t

    def flush(self):
        pass


def make_blocks(specs):
    return [
        {"id": f"b{i}", "type": btype, "tokens": tok, "priority": "medium", "compacted": False}
        for i, (btype, tok) in enumerate(specs)
    ]


class FakePack:
    def __init__(self, blocks, budget=8000):
        self.blocks = blocks
        self.budget = budget


class TestTokenPakTracer:
    def test_trace_pack_creates_trace(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack(make_blocks([("knowledge", 400), ("instructions", 100)]))

        with tracer.trace_pack(pack, name="test_rag") as span:
            assert span is not None

        assert len(lf.traces) == 1
        trace = lf.traces[0]
        assert trace.init_kwargs["name"] == "test_rag"
        assert trace.init_kwargs["input"]["type"] == "tokenpak_pack"
        assert trace.init_kwargs["input"]["tokenpak"]["total_tokens"] == 500
        assert "tokenpak" in trace.init_kwargs["tags"]

    def test_trace_pack_with_budget(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack(make_blocks([("knowledge", 500)]), budget=2000)

        with tracer.trace_pack(pack, name="budget_test") as span:
            pass

        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta["budget"] == 2000
        assert meta["utilization_pct"] == 25.0

    def test_trace_pack_with_raw_tokens(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack(make_blocks([("evidence", 300)]))

        with tracer.trace_pack(pack, raw_tokens=600) as span:
            pass

        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta["raw_tokens"] == 600
        assert meta["tokens_saved"] == 300
        assert meta["compression_ratio"] == 0.5

    def test_record_output_string(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack(make_blocks([("instructions", 100)]))

        with tracer.trace_pack(pack) as span:
            tracer.record_output(span, "This is the response.")

        assert lf.traces[0].updated["output"] == "This is the response."

    def test_record_output_object_with_text(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        pack = FakePack(make_blocks([("instructions", 100)]))

        class FakeResponse:
            text = "Response text."

        with tracer.trace_pack(pack) as span:
            tracer.record_output(span, FakeResponse())

        assert lf.traces[0].updated["output"] == "Response text."

    def test_langfuse_failure_is_silent(self):
        class BrokenLangfuse:
            def trace(self, **kwargs):
                raise RuntimeError("Langfuse down")
            def flush(self):
                pass

        tracer = TokenPakTracer(BrokenLangfuse())
        pack = FakePack(make_blocks([("knowledge", 100)]))

        # Should not raise
        with tracer.trace_pack(pack) as span:
            assert span is None  # trace failed gracefully

    def test_analytics_recorded(self):
        lf = MockLangfuse()
        analytics = TokenPakAnalytics()
        tracer = TokenPakTracer(lf, analytics=analytics)
        pack = FakePack(make_blocks([("knowledge", 400)]))

        with tracer.trace_pack(pack, raw_tokens=800) as span:
            pass

        report = tracer.get_analytics()
        assert report["pack_count"] == 1
        assert report["tokens_saved"] == 400

    def test_ascii_summary_opt_in(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf, trace_ascii_summary=True)
        pack = FakePack(make_blocks([("knowledge", 200)]))

        with tracer.trace_pack(pack) as span:
            pass

        input_data = lf.traces[0].init_kwargs["input"]
        assert "ascii_summary" in input_data
        assert "TokenPak Pack" in input_data["ascii_summary"]

    def test_trace_from_list_of_blocks(self):
        lf = MockLangfuse()
        tracer = TokenPakTracer(lf)
        blocks = make_blocks([("memory", 50), ("conversation", 100)])

        with tracer.trace_pack(blocks, name="list_test") as span:
            pass

        meta = lf.traces[0].init_kwargs["input"]["tokenpak"]
        assert meta["total_tokens"] == 150
        assert meta["block_count"] == 2
