# tokenpak-vectordb

> Convert vector DB query results directly into TokenPak blocks.

**tokenpak-vectordb** provides adapters for the most popular vector databases, turning retrieval results into structured `VectorBlock` objects compatible with the TokenPak protocol.

## Why?

```
# Before: raw results → custom formatting → prompt
results = index.query(embedding, top_k=10)
context = "\n".join(r.metadata["text"] for r in results.matches)
prompt = f"Context:\n{context}\n\nQuestion: {query}"

# After: retrieval results become TokenPak blocks automatically
from tokenpak_vectordb import PineconeAdapter
adapter = PineconeAdapter(index)
blocks = adapter.query_as_blocks(embedding, top_k=10)
# Each block: .quality (similarity score), .content (text), .provenance (source info)
```

## Supported Databases

| Adapter | Database | Client Version |
|---------|----------|---------------|
| `PineconeAdapter` | Pinecone | pinecone-client v3+ |
| `WeaviateAdapter` | Weaviate | v3 and v4 |
| `QdrantAdapter` | Qdrant | qdrant-client v1.6+ |
| `ChromaAdapter` | Chroma | chromadb v0.4+ |

## Installation

```bash
# All adapters
pip install tokenpak-vectordb[all]

# Specific adapter only
pip install tokenpak-vectordb[pinecone]
pip install tokenpak-vectordb[weaviate]
pip install tokenpak-vectordb[qdrant]
pip install tokenpak-vectordb[chroma]
```

## Quick Start

### Pinecone

```python
from pinecone import Pinecone
from tokenpak_vectordb import PineconeAdapter

pc = Pinecone(api_key="...")
index = pc.Index("my-index")
adapter = PineconeAdapter(index)

query_embedding = embed("What is context compression?")
blocks = adapter.query_as_blocks(query_embedding, top_k=10)

for block in blocks:
    print(f"[{block.quality:.2f}] {block.content[:80]}")
    # [0.95] TokenPak is a protocol for context compression in RAG pipelines...
```

### Weaviate

```python
import weaviate
from tokenpak_vectordb import WeaviateAdapter

client = weaviate.connect_to_local()
adapter = WeaviateAdapter(client, collection_name="Document")

# Semantic search from text
blocks = adapter.query_as_blocks("What is TokenPak?", limit=10)

# Or from vector embedding
blocks = adapter.query_as_blocks(my_embedding, mode="near_vector", limit=10)
```

### Qdrant

```python
from qdrant_client import QdrantClient
from tokenpak_vectordb import QdrantAdapter

client = QdrantClient("localhost", port=6333)
adapter = QdrantAdapter(client, collection_name="docs")

blocks = adapter.query_as_blocks(query_vector, limit=10)
```

### Chroma

```python
import chromadb
from tokenpak_vectordb import ChromaAdapter

client = chromadb.Client()
collection = client.get_collection("docs")
adapter = ChromaAdapter(collection)

# Text query
blocks = adapter.query_as_blocks("TokenPak context compression", limit=10)

# Vector query
blocks = adapter.query_as_blocks(embedding, limit=10)
```

## VectorBlock

Every adapter returns a list of `VectorBlock` objects:

```python
block.id          # str  — result ID from the vector DB
block.content     # str  — document text
block.block_type  # str  — "evidence", "knowledge", etc.
block.quality     # float (0-1) — similarity score, normalized
block.tokens      # int  — estimated token count
block.metadata    # dict — raw metadata from vector DB (content field removed)
block.provenance  # dict — source attribution with timestamp

block.to_dict()   # serialize to TokenPak wire format
block.truncate(n) # return new block with content truncated to n tokens
```

## Batch Queries

```python
queries = [embed("query 1"), embed("query 2"), embed("query 3")]
result = adapter.batch_query_as_blocks(queries, limit=5)

result.flat_blocks          # all blocks from all queries
result[0]                   # blocks for query 1
result.elapsed_ms           # total time in ms
```

## Full RAG Pipeline

```python
from pinecone import Pinecone
from tokenpak_vectordb import PineconeAdapter

# Setup
pc = Pinecone(api_key="...")
adapter = PineconeAdapter(pc.Index("knowledge-base"))

# Retrieve as VectorBlocks
query = "How does context compression work?"
evidence = adapter.query_as_blocks(embed(query), top_k=5)

# Build prompt with quality-ordered context
context = "\n\n".join(
    f"[Source {b.provenance['source_id']} | score={b.quality:.2f}]\n{b.content}"
    for b in sorted(evidence, key=lambda b: b.quality, reverse=True)
)

prompt = f"Context:\n{context}\n\nQuestion: {query}"
response = llm.complete(prompt)
```

## Score → Quality Mapping

| Database | Raw Score | Mapping |
|----------|-----------|---------|
| Pinecone | Cosine similarity (0-1) | Direct |
| Weaviate | Certainty (0-1) | Direct |
| Weaviate | Distance (0-2) | `1 - distance/2` |
| Qdrant (cosine) | Score (-1 to 1) | `(score+1)/2` |
| Qdrant (euclid) | Distance (0+) | `1/(1+distance)` |
| Qdrant (dot) | Unbounded | Sigmoid |
| Chroma (L2) | Distance (0+) | `1/(1+distance)` |
| Chroma (cosine) | Distance (0-2) | `1 - distance/2` |

## License

MIT
