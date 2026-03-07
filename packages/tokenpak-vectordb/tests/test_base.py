"""Tests for base VectorBlock and VectorDBAdapter."""

import pytest
from tokenpak_vectordb.base import VectorBlock, VectorDBAdapter, BatchQueryResult


# ---------------------------------------------------------------------------
# VectorBlock tests
# ---------------------------------------------------------------------------

class TestVectorBlock:
    def test_basic_creation(self):
        b = VectorBlock(id="doc1", content="Hello world")
        assert b.id == "doc1"
        assert b.content == "Hello world"
        assert b.block_type == "evidence"
        assert b.quality == 1.0
        assert b.tokens > 0

    def test_token_estimation(self):
        b = VectorBlock(id="x", content="a" * 400)
        assert b.tokens == 100

    def test_quality_clamping(self):
        b = VectorBlock(id="x", content="hi", quality=1.5)
        assert b.quality == 1.0
        b2 = VectorBlock(id="x", content="hi", quality=-0.5)
        assert b2.quality == 0.0

    def test_to_dict(self):
        b = VectorBlock(
            id="doc1",
            content="Test content",
            block_type="knowledge",
            quality=0.9,
            metadata={"source": "wiki"},
            provenance={"source_type": "pinecone"},
        )
        d = b.to_dict()
        assert d["id"] == "doc1"
        assert d["type"] == "knowledge"
        assert d["quality"] == 0.9
        assert d["metadata"] == {"source": "wiki"}

    def test_truncate(self):
        long_content = "word " * 200  # ~800 chars, ~200 tokens
        b = VectorBlock(id="x", content=long_content)
        truncated = b.truncate(50)
        assert truncated.tokens <= 52  # some slack for estimation
        assert truncated.compressed
        assert "truncated" in truncated.provenance

    def test_truncate_no_op_if_small(self):
        b = VectorBlock(id="x", content="small")
        result = b.truncate(1000)
        assert result is b  # same object returned

    def test_repr(self):
        b = VectorBlock(id="doc1", content="Test content here")
        r = repr(b)
        assert "doc1" in r
        assert "evidence" in r

    def test_custom_block_type(self):
        b = VectorBlock(id="x", content="hi", block_type="knowledge")
        assert b.block_type == "knowledge"

    def test_metadata_default_empty(self):
        b = VectorBlock(id="x", content="hi")
        assert b.metadata == {}
        assert b.provenance == {}


# ---------------------------------------------------------------------------
# BatchQueryResult tests
# ---------------------------------------------------------------------------

class TestBatchQueryResult:
    def _make_blocks(self, n: int, prefix: str = "doc") -> list:
        return [VectorBlock(id=f"{prefix}{i}", content=f"content {i}") for i in range(n)]

    def test_flat_blocks(self):
        r = BatchQueryResult(
            queries=["q1", "q2"],
            results=[self._make_blocks(3, "a"), self._make_blocks(2, "b")],
        )
        assert len(r.flat_blocks) == 5

    def test_len(self):
        r = BatchQueryResult(queries=["q1"], results=[self._make_blocks(2)])
        assert len(r) == 1

    def test_getitem(self):
        blocks = self._make_blocks(3)
        r = BatchQueryResult(queries=["q1"], results=[blocks])
        assert r[0] == blocks


# ---------------------------------------------------------------------------
# VectorDBAdapter abstract interface test
# ---------------------------------------------------------------------------

class ConcreteAdapter(VectorDBAdapter):
    """Minimal concrete implementation for testing base class."""
    source_type = "test"

    def query_as_blocks(self, query, limit=None, block_type=None, **kwargs):
        n = limit or self.default_limit
        btype = block_type or self.default_block_type
        return [
            VectorBlock(
                id=f"result_{i}",
                content=f"Content for result {i}",
                block_type=btype,
                quality=1.0 - i * 0.1,
            )
            for i in range(n)
        ]

    def _result_to_block(self, result, block_type):
        return None


class TestVectorDBAdapterBase:
    def test_batch_query(self):
        adapter = ConcreteAdapter()
        result = adapter.batch_query_as_blocks(["q1", "q2"], limit=3)
        assert len(result) == 2
        assert len(result[0]) == 3
        assert result.elapsed_ms >= 0

    def test_score_to_quality_cosine(self):
        a = ConcreteAdapter()
        assert a._score_to_quality(0.9) == 0.9
        assert a._score_to_quality(1.5) == 1.0  # clamped
        assert a._score_to_quality(-0.5) == 0.0  # clamped

    def test_score_to_quality_inverted(self):
        a = ConcreteAdapter()
        q = a._score_to_quality(0.0, invert=True)
        assert q == 1.0
        q2 = a._score_to_quality(1.0, invert=True)
        assert q2 == 0.5

    def test_make_provenance(self):
        a = ConcreteAdapter()
        prov = a._make_provenance("doc1", extra={"collection": "test"})
        assert prov["source_type"] == "test"
        assert prov["source_id"] == "doc1"
        assert "retrieved_at" in prov
        assert prov["collection"] == "test"

    def test_extract_content_from_text_field(self):
        a = ConcreteAdapter()
        payload = {"text": "Hello world", "other": "data"}
        assert a._extract_content(payload) == "Hello world"

    def test_extract_content_fallback(self):
        a = ConcreteAdapter()
        payload = {"body": "Document body here"}
        # "body" not in default fields, should join all strings
        result = a._extract_content(payload)
        assert "Document body here" in result

    def test_extract_content_custom_field(self):
        a = ConcreteAdapter(content_field="chunk")
        payload = {"chunk": "Chunk content", "text": "Other text"}
        assert a._extract_content(payload) == "Chunk content"

    def test_default_params(self):
        a = ConcreteAdapter()
        assert a.default_block_type == "evidence"
        assert a.default_limit == 10
