"""
tokenpak-vectordb — TokenPak adapters for vector databases.

Convert vector DB query results directly into TokenPak blocks for seamless
RAG pipelines.

Supported adapters:
  - PineconeAdapter   → Pinecone (pinecone-client v3+)
  - WeaviateAdapter   → Weaviate (v3 and v4 client)
  - QdrantAdapter     → Qdrant (qdrant-client v1.6+)
  - ChromaAdapter     → Chroma (chromadb v0.4+)

Core types:
  - VectorBlock       → portable TokenPak block for retrieval results
  - VectorDBAdapter   → abstract base class
  - BatchQueryResult  → container for batch query results

Quick start:
    from tokenpak_vectordb import PineconeAdapter, VectorBlock

    adapter = PineconeAdapter(pinecone_index)
    blocks = adapter.query_as_blocks(query_embedding, top_k=10)
    # blocks: List[VectorBlock], each with .quality, .content, .provenance
"""

from .base import VectorBlock, VectorDBAdapter, BatchQueryResult
from .pinecone import PineconeAdapter
from .weaviate import WeaviateAdapter
from .qdrant import QdrantAdapter
from .chroma import ChromaAdapter

__version__ = "0.1.0"
__all__ = [
    # Core types
    "VectorBlock",
    "VectorDBAdapter",
    "BatchQueryResult",
    # Adapters
    "PineconeAdapter",
    "WeaviateAdapter",
    "QdrantAdapter",
    "ChromaAdapter",
    # Version
    "__version__",
]
