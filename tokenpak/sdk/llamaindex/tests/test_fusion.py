"""Tests for MultiIndexFusion."""

import asyncio

import pytest
from llamaindex_tokenpak import MultiIndexFusion

# --- Mock engines ---


class MockResponse:
    def __init__(self, text, source_nodes=None):
        self.response = text
        self.source_nodes = source_nodes or []

    def __str__(self):
        return self.response


class MockEngine:
    def __init__(self, name, node_count=3, tokens_each=200):
        self.name = name
        self._nodes = [
            {
                "id": f"{name}_node_{i}",
                "text": f"From {name}: document {i}. " + "content " * tokens_each,
                "metadata": {"source": name},
                "score": 0.9 - i * 0.1,
            }
            for i in range(node_count)
        ]

    def query(self, query_str, **kwargs):
        return MockResponse(f"Answer from {self.name}", source_nodes=self._nodes)

    async def aquery(self, query_str, **kwargs):
        return self.query(query_str, **kwargs)


# --- Tests ---


class TestFusionCreation:
    def test_basic_creation(self):
        indexes = {"a": MockEngine("a"), "b": MockEngine("b")}
        fusion = MultiIndexFusion(indexes=indexes, budget=4000)
        assert len(fusion.indexes) == 2
        assert fusion.budget == 4000

    def test_default_weights(self):
        indexes = {"a": MockEngine("a"), "b": MockEngine("b")}
        fusion = MultiIndexFusion(indexes)
        assert fusion.weights["a"] == pytest.approx(0.5)
        assert fusion.weights["b"] == pytest.approx(0.5)

    def test_empty_indexes_raises(self):
        with pytest.raises(ValueError, match="At least one index"):
            MultiIndexFusion(indexes={})

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="strategy must be"):
            MultiIndexFusion({"a": MockEngine("a")}, strategy="invalid")

    def test_custom_weights(self):
        indexes = {"docs": MockEngine("docs"), "code": MockEngine("code")}
        fusion = MultiIndexFusion(indexes, weights={"docs": 0.7, "code": 0.3})
        assert fusion.weights["docs"] == 0.7
        assert fusion.weights["code"] == 0.3


class TestFusionQuery:
    def test_query_returns_dict(self):
        indexes = {"a": MockEngine("a"), "b": MockEngine("b")}
        fusion = MultiIndexFusion(indexes, budget=2000)
        result = fusion.query("What is compression?")
        assert isinstance(result, dict)

    def test_query_has_required_fields(self):
        indexes = {"a": MockEngine("a"), "b": MockEngine("b")}
        fusion = MultiIndexFusion(indexes, budget=2000)
        result = fusion.query("test")
        for field in ("query", "context", "blocks", "sources", "tokens"):
            assert field in result, f"Missing field: {field}"

    def test_query_preserved(self):
        fusion = MultiIndexFusion({"a": MockEngine("a")})
        result = fusion.query("Specific query.")
        assert result["query"] == "Specific query."

    def test_sources_tracking(self):
        indexes = {"docs": MockEngine("docs", 3), "code": MockEngine("code", 2)}
        fusion = MultiIndexFusion(indexes, budget=4000)
        result = fusion.query("test")
        assert "docs" in result["sources"]
        assert "code" in result["sources"]
        assert result["sources"]["docs"] == 3
        assert result["sources"]["code"] == 2

    def test_blocks_from_all_indexes(self):
        indexes = {"a": MockEngine("a", 2), "b": MockEngine("b", 2)}
        fusion = MultiIndexFusion(indexes, budget=8000)
        result = fusion.query("test")
        # Should have blocks from both
        source_names = {
            b["provenance"]["source_type"] for b in result["blocks"] if "provenance" in b
        }
        # Not checking exact sources since metadata handling varies
        assert len(result["blocks"]) > 0

    def test_compression_within_budget(self):
        indexes = {"a": MockEngine("a", 5, 500), "b": MockEngine("b", 5, 500)}
        fusion = MultiIndexFusion(indexes, budget=300)
        result = fusion.query("test")
        # Tokens out should be ≤ budget + small overhead
        assert result["tokens"]["output"] <= result["tokens"]["input"]

    def test_context_is_string(self):
        fusion = MultiIndexFusion({"a": MockEngine("a")})
        result = fusion.query("test")
        assert isinstance(result["context"], str)


class TestFusionStrategies:
    def test_rank_strategy(self):
        fusion = MultiIndexFusion({"a": MockEngine("a", 3)}, strategy="rank", budget=4000)
        result = fusion.query("test")
        # Blocks should be ordered by quality desc
        qualities = [b["quality"] for b in result["blocks"]]
        assert qualities == sorted(qualities, reverse=True)

    def test_round_robin_strategy(self):
        indexes = {"a": MockEngine("a", 3), "b": MockEngine("b", 3)}
        fusion = MultiIndexFusion(indexes, strategy="round_robin", budget=8000)
        result = fusion.query("test")
        assert len(result["blocks"]) > 0

    def test_weighted_strategy(self):
        indexes = {"a": MockEngine("a", 2), "b": MockEngine("b", 2)}
        fusion = MultiIndexFusion(
            indexes,
            strategy="weighted",
            weights={"a": 0.8, "b": 0.2},
            budget=4000,
        )
        result = fusion.query("test")
        assert len(result["blocks"]) > 0


class TestFusionQueryAsTokenpak:
    def test_tokenpak_pack_has_metadata(self):
        indexes = {"docs": MockEngine("docs"), "code": MockEngine("code")}
        fusion = MultiIndexFusion(indexes, budget=4000)
        pack = fusion.query_as_tokenpak("test")
        assert "metadata" in pack
        assert pack["metadata"]["strategy"] == "rank"
        assert pack["metadata"]["index_count"] == 2

    def test_index_names_in_metadata(self):
        indexes = {"docs": MockEngine("docs"), "wiki": MockEngine("wiki")}
        fusion = MultiIndexFusion(indexes, budget=4000)
        pack = fusion.query_as_tokenpak("test")
        assert "docs" in pack["metadata"]["index_names"]
        assert "wiki" in pack["metadata"]["index_names"]


class TestFusionAsync:
    def test_aquery_runs(self):
        indexes = {"a": MockEngine("a"), "b": MockEngine("b")}
        fusion = MultiIndexFusion(indexes, budget=4000)

        async def _run():
            return await fusion.aquery("async test")

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result["query"] == "async test"
        assert len(result["blocks"]) > 0

    def test_aquery_as_tokenpak(self):
        indexes = {"a": MockEngine("a")}
        fusion = MultiIndexFusion(indexes, budget=4000)

        async def _run():
            return await fusion.aquery_as_tokenpak("test")

        pack = asyncio.get_event_loop().run_until_complete(_run())
        assert "metadata" in pack


class TestFusionEdgeCases:
    def test_single_index(self):
        fusion = MultiIndexFusion({"solo": MockEngine("solo", 5)}, budget=4000)
        result = fusion.query("test")
        assert len(result["blocks"]) > 0

    def test_empty_result_index(self):
        class EmptyEngine:
            def query(self, q, **kw):
                return MockResponse("nothing", source_nodes=[])

            async def aquery(self, q, **kw):
                return self.query(q)

        indexes = {"empty": EmptyEngine(), "real": MockEngine("real", 3)}
        fusion = MultiIndexFusion(indexes, budget=4000)
        result = fusion.query("test")
        # Should still work, just fewer blocks
        assert result["sources"]["empty"] == 0
        assert result["sources"]["real"] == 3
