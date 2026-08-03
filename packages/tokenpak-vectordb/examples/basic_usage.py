"""
tokenpak-vectordb — Basic Usage Examples

Shows how to use each adapter with mock data (no real DB connection needed).
"""

from unittest.mock import MagicMock
from tokenpak_vectordb import (
    PineconeAdapter, WeaviateAdapter, QdrantAdapter, ChromaAdapter,
    VectorBlock
)


# ============================================================
# 1. Pinecone Adapter
# ============================================================
print("=" * 60)
print("1. Pinecone Adapter")
print("=" * 60)

# Mock a Pinecone index
pinecone_index = MagicMock()
matches = []
for i, (score, text) in enumerate([
    (0.95, "TokenPak is a protocol for context compression in RAG pipelines."),
    (0.87, "Vector databases store embeddings for semantic search."),
    (0.72, "Context windows limit how much text LLMs can process."),
]):
    m = MagicMock()
    m.id = f"doc_{i}"
    m.score = score
    m.metadata = {"text": text, "source": f"wiki-{i}"}
    matches.append(m)

response = MagicMock()
response.matches = matches
pinecone_index.query.return_value = response
pinecone_index.name = "knowledge-base"

adapter = PineconeAdapter(pinecone_index)
query_embedding = [0.1] * 1536  # typical OpenAI embedding size

blocks = adapter.query_as_blocks(query_embedding, limit=3, block_type="evidence")
print(f"Retrieved {len(blocks)} blocks from Pinecone:\n")
for block in blocks:
    print(f"  [{block.quality:.2f}] {block.content[:60]}...")
    print(f"         provenance: source_id={block.provenance['source_id']}")
print()


# ============================================================
# 2. Weaviate Adapter (v4 style)
# ============================================================
print("=" * 60)
print("2. Weaviate Adapter (v4)")
print("=" * 60)

weaviate_client = MagicMock()
objs = []
for i, (certainty, text) in enumerate([
    (0.91, "LlamaIndex provides retrieval pipelines for RAG."),
    (0.78, "Weaviate supports hybrid search with BM25 and vectors."),
]):
    obj = MagicMock()
    obj.properties = {"text": text, "category": "tech"}
    obj.uuid = f"wv-uuid-{i}"
    meta = MagicMock()
    meta.certainty = certainty
    meta.distance = None
    obj.metadata = meta
    objs.append(obj)

wv_response = MagicMock()
wv_response.objects = objs
weaviate_client.collections.get.return_value.query.near_text.return_value = wv_response

adapter = WeaviateAdapter(weaviate_client, collection_name="Document")
blocks = adapter.query_as_blocks("What is TokenPak?", limit=5)
print(f"Retrieved {len(blocks)} blocks from Weaviate:\n")
for block in blocks:
    print(f"  [{block.quality:.2f}] {block.content[:60]}...")
print()


# ============================================================
# 3. Qdrant Adapter
# ============================================================
print("=" * 60)
print("3. Qdrant Adapter (cosine)")
print("=" * 60)

qdrant_client = MagicMock()
hits = []
for i, (score, text) in enumerate([
    (0.92, "Qdrant is a high-performance vector search engine."),
    (0.85, "Approximate nearest neighbor search enables fast retrieval."),
    (0.70, "Metadata filtering lets you combine keyword and semantic search."),
]):
    h = MagicMock()
    h.id = str(i + 1)
    h.score = score
    h.payload = {"text": text, "lang": "en"}
    hits.append(h)
qdrant_client.search.return_value = hits

adapter = QdrantAdapter(qdrant_client, collection_name="docs", score_metric="cosine")
blocks = adapter.query_as_blocks([0.1] * 768, limit=3)
print(f"Retrieved {len(blocks)} blocks from Qdrant:\n")
for block in blocks:
    # cosine: quality = (score+1)/2
    print(f"  [{block.quality:.2f}] {block.content[:60]}...")
print()


# ============================================================
# 4. Chroma Adapter
# ============================================================
print("=" * 60)
print("4. Chroma Adapter")
print("=" * 60)

chroma_collection = MagicMock()
chroma_collection.name = "research-papers"
chroma_collection.query.return_value = {
    "ids": [["chroma-1", "chroma-2"]],
    "documents": [
        [
            "Retrieval-augmented generation (RAG) combines retrieval with generation.",
            "Context compression reduces token costs while preserving quality.",
        ]
    ],
    "distances": [[0.15, 0.42]],  # L2 distances
    "metadatas": [[{"year": 2023}, {"year": 2024}]],
}

adapter = ChromaAdapter(chroma_collection, distance_metric="l2")
blocks = adapter.query_as_blocks("RAG context compression", limit=2)
print(f"Retrieved {len(blocks)} blocks from Chroma:\n")
for block in blocks:
    print(f"  [{block.quality:.2f}] {block.content[:60]}...")
    print(f"         year={block.metadata.get('year')}")
print()


# ============================================================
# 5. Batch Query
# ============================================================
print("=" * 60)
print("5. Batch Query")
print("=" * 60)

adapter = PineconeAdapter(pinecone_index)
queries = [[0.1] * 1536, [0.2] * 1536, [0.3] * 1536]
result = adapter.batch_query_as_blocks(queries, limit=2)
print(f"Batch query: {len(queries)} queries")
print(f"Total blocks returned: {len(result.flat_blocks)}")
print(f"Elapsed: {result.elapsed_ms:.1f} ms")
for i, query_blocks in enumerate(result.results):
    print(f"  Query {i+1}: {len(query_blocks)} blocks")
print()


# ============================================================
# 6. VectorBlock utilities
# ============================================================
print("=" * 60)
print("6. VectorBlock utilities")
print("=" * 60)

block = VectorBlock(
    id="demo-1",
    content="TokenPak is a protocol for deterministic context compression in RAG pipelines. " * 5,
    block_type="evidence",
    quality=0.93,
    metadata={"source": "docs"},
    provenance={"source_type": "pinecone", "source_id": "demo-1"},
)
print(f"Original block: {block.tokens} tokens")
truncated = block.truncate(20)
print(f"Truncated to 20: {truncated.tokens} tokens, compressed={truncated.compressed}")
print(f"Wire format keys: {list(block.to_dict().keys())}")
print()
print("Done! All adapters working.")
