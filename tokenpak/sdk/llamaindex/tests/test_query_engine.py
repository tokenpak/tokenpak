"""Tests for TokenPakQueryEngine."""

import asyncio

from llamaindex_tokenpak import TokenPakQueryEngine

# --- Mock query engine ---


class MockResponse:
    """Simulates a LlamaIndex Response with source nodes."""

    def __init__(self, text="Answer text.", nodes=None):
        self.response = text
        self.source_nodes = nodes or []

    def __str__(self):
        return self.response


class MockQueryEngine:
    """Minimal LlamaIndex-compatible query engine."""

    def __init__(self, response_text="Mock answer.", source_nodes=None):
        self._response_text = response_text
        self._source_nodes = source_nodes or []

    def query(self, query_str, **kwargs):
        return MockResponse(self._response_text, self._source_nodes)

    async def aquery(self, query_str, **kwargs):
        return MockResponse(self._response_text, self._source_nodes)


def make_source_nodes(count=3, tokens_each=500):
    return [
        {
            "id": f"src_{i}",
            "text": f"Evidence document {i}. " + "content " * tokens_each,
            "metadata": {"file_name": f"doc{i}.md"},
            "score": 0.9 - i * 0.1,
        }
        for i in range(count)
    ]


# --- Tests ---


class TestQueryEngineCreation:
    def test_basic_creation(self):
        engine = MockQueryEngine()
        tp = TokenPakQueryEngine(query_engine=engine, budget=4000)
        assert tp.budget == 4000

    def test_default_budget(self):
        tp = TokenPakQueryEngine(query_engine=MockQueryEngine())
        assert tp.budget == 4000


class TestQuery:
    def test_query_returns_response(self):
        engine = MockQueryEngine(response_text="Test answer.")
        tp = TokenPakQueryEngine(query_engine=engine)
        result = tp.query("test question")
        assert str(result) == "Test answer."

    def test_aquery_returns_response(self):
        engine = MockQueryEngine(response_text="Async answer.")
        tp = TokenPakQueryEngine(query_engine=engine)

        async def _run():
            return await tp.aquery("question")

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert str(result) == "Async answer."


class TestQueryAsTokenpak:
    def test_pack_has_required_fields(self):
        nodes = make_source_nodes(3)
        engine = MockQueryEngine(source_nodes=nodes)
        tp = TokenPakQueryEngine(query_engine=engine, budget=2000)

        pack = tp.query_as_tokenpak("What is compression?")
        assert "query" in pack
        assert "context" in pack
        assert "blocks" in pack
        assert "tokens" in pack
        assert "source_nodes" in pack
        assert "raw_response" in pack

    def test_query_preserved(self):
        tp = TokenPakQueryEngine(query_engine=MockQueryEngine())
        pack = tp.query_as_tokenpak("Specific question here?")
        assert pack["query"] == "Specific question here?"

    def test_context_is_string(self):
        nodes = make_source_nodes(2)
        engine = MockQueryEngine(source_nodes=nodes)
        tp = TokenPakQueryEngine(query_engine=engine, budget=1000)
        pack = tp.query_as_tokenpak("test")
        assert isinstance(pack["context"], str)

    def test_blocks_are_dicts(self):
        nodes = make_source_nodes(3)
        engine = MockQueryEngine(source_nodes=nodes)
        tp = TokenPakQueryEngine(query_engine=engine, budget=2000)
        pack = tp.query_as_tokenpak("test")
        assert isinstance(pack["blocks"], list)
        for block in pack["blocks"]:
            assert "content" in block
            assert "quality" in block
            assert "tokens" in block

    def test_token_stats_present(self):
        nodes = make_source_nodes(3)
        engine = MockQueryEngine(source_nodes=nodes)
        tp = TokenPakQueryEngine(query_engine=engine, budget=500)
        pack = tp.query_as_tokenpak("test")
        tokens = pack["tokens"]
        assert "input" in tokens
        assert "output" in tokens
        assert "budget" in tokens
        assert "ratio" in tokens
        assert tokens["budget"] == 500

    def test_compression_applied(self):
        nodes = make_source_nodes(5, tokens_each=1000)
        engine = MockQueryEngine(source_nodes=nodes)
        tp = TokenPakQueryEngine(query_engine=engine, budget=200)
        pack = tp.query_as_tokenpak("test")
        # Output should be ≤ budget * 4 chars roughly
        total_content = sum(len(b["content"]) for b in pack["blocks"])
        assert total_content < sum(1000 * 8 for _ in nodes)  # less than uncompressed

    def test_no_source_nodes_uses_response(self):
        """When engine returns no source nodes, response text becomes the block."""
        engine = MockQueryEngine(response_text="The answer is 42.", source_nodes=[])
        tp = TokenPakQueryEngine(query_engine=engine)
        pack = tp.query_as_tokenpak("test")
        assert len(pack["blocks"]) >= 1
        combined_content = " ".join(b["content"] for b in pack["blocks"])
        assert "42" in combined_content

    def test_async_query_as_tokenpak(self):
        nodes = make_source_nodes(2)
        engine = MockQueryEngine(source_nodes=nodes)
        tp = TokenPakQueryEngine(query_engine=engine, budget=1000)

        async def _run():
            return await tp.aquery_as_tokenpak("async test")

        pack = asyncio.get_event_loop().run_until_complete(_run())
        assert pack["query"] == "async test"
        assert "blocks" in pack

    def test_extra_nodes_included(self):
        engine = MockQueryEngine(source_nodes=[])
        extra = [
            {
                "id": "extra_0",
                "text": "Extra context info.",
                "metadata": {},
                "score": 0.8,
            }
        ]
        tp = TokenPakQueryEngine(query_engine=engine, budget=4000)
        pack = tp.query_as_tokenpak("test", extra_nodes=extra)
        # Extra nodes should show up in blocks or be incorporated
        assert len(pack["blocks"]) >= 1
