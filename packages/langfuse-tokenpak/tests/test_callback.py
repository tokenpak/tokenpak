"""Tests for langfuse_tokenpak.callback handlers."""

import pytest
from langfuse_tokenpak.callback import (
    TokenPakCallbackHandler,
    TokenPakLangChainCallback,
    TokenPakLlamaIndexCallback,
)


class MockLangfuse:
    def __init__(self):
        self.traces = []

    def trace(self, **kwargs):
        self.traces.append(kwargs)
        return None

    def flush(self):
        pass


def make_block(btype="knowledge", tokens=100):
    return {"id": "b1", "type": btype, "tokens": tokens, "priority": "medium", "compacted": False}


class FakeCompiledPack:
    def __init__(self, blocks, total_tokens=None, compression_ratio=1.0):
        self.blocks = blocks
        self.total_tokens = total_tokens or sum(b["tokens"] for b in blocks)
        self.compression_ratio = compression_ratio


class FakePack:
    def __init__(self, blocks, budget=8000):
        self.blocks = blocks
        self.budget = budget


class TestTokenPakCallbackHandler:
    def test_on_tokenpak_compile(self):
        lf = MockLangfuse()
        handler = TokenPakCallbackHandler(lf)
        pack = FakePack([make_block("knowledge", 400)])
        compiled = FakeCompiledPack([make_block("knowledge", 400)], compression_ratio=0.5)

        handler.on_tokenpak_compile(pack, compiled)

        assert len(lf.traces) == 1
        trace = lf.traces[0]
        assert trace["name"] == "tokenpak_compile"
        assert trace["input"]["type"] == "tokenpak_compile"
        assert "tokenpak" in trace["input"]

    def test_analytics_recorded(self):
        lf = MockLangfuse()
        handler = TokenPakCallbackHandler(lf)
        pack = FakePack([make_block("knowledge", 400)])
        compiled = FakeCompiledPack([make_block("knowledge", 400)])
        handler.on_tokenpak_compile(pack, compiled)
        report = handler.analytics.get_report()
        assert report["pack_count"] == 1


class TestTokenPakLangChainCallback:
    def test_on_chain_start_injects_tokenpak(self):
        received_inputs = []

        class MockLangfuseHandler:
            def on_chain_start(self, serialized, inputs, **kwargs):
                received_inputs.append(inputs)

        cb = TokenPakLangChainCallback(langfuse_handler=MockLangfuseHandler())
        cb.on_tokenpak_pack(FakePack([make_block("knowledge", 100)]))
        cb.on_chain_start({}, {"question": "What?"})

        assert len(received_inputs) == 1
        assert "_tokenpak" in received_inputs[0]

    def test_pack_meta_cleared_after_chain_end(self):
        cb = TokenPakLangChainCallback()
        cb.on_tokenpak_pack(FakePack([make_block("instructions", 50)]))
        assert cb._current_pack_meta is not None
        cb.on_chain_end({"output": "ok"})
        assert cb._current_pack_meta is None

    def test_no_langfuse_handler_no_crash(self):
        cb = TokenPakLangChainCallback()
        cb.on_chain_start({}, {})
        cb.on_chain_end({})


class TestTokenPakLlamaIndexCallback:
    def test_captures_pack_metadata(self):
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf)
        pack = FakePack([make_block("evidence", 300)])

        cb.on_event_start("query", payload={"tokenpak_pack": pack}, event_id="evt1")
        cb.on_event_end("query", event_id="evt1")

        assert len(lf.traces) == 1
        trace = lf.traces[0]
        assert "llamaindex_query" == trace["name"]
        assert "tokenpak" in trace["tags"]

    def test_no_pack_in_payload_no_trace(self):
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf)
        cb.on_event_start("query", payload={"some_key": "value"}, event_id="evt2")
        cb.on_event_end("query", event_id="evt2")
        assert len(lf.traces) == 0

    def test_ignored_events_skipped(self):
        lf = MockLangfuse()
        cb = TokenPakLlamaIndexCallback(lf, event_starts_to_ignore=["chunking"])
        pack = FakePack([make_block("knowledge", 100)])
        cb.on_event_start("chunking", payload={"tokenpak_pack": pack}, event_id="evt3")
        # Even if event was registered, ignored events shouldn't trace
        # (In our impl, start is ignored so pack not stored → no trace)
        cb.on_event_end("chunking", event_id="evt3")
        assert len(lf.traces) == 0
