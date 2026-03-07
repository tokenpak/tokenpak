"""
Node ↔ TokenPak Block conversion utilities for LlamaIndex.

Converts between LlamaIndex Node format and TokenPak Block format,
supporting both dict-style nodes and real LlamaIndex TextNode/NodeWithScore
objects when llama-index-core is installed.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Lightweight Block representation (no hard dep on tokenpak-sdk)
# ---------------------------------------------------------------------------

@dataclass
class LlamaBlock:
    """
    Portable Block representation compatible with TokenPak protocol.

    Maps LlamaIndex node data to TokenPak block semantics:
      - id          → node id
      - content     → node text
      - quality     → retrieval score (0-1)
      - tokens      → estimated token count
      - metadata    → node metadata
      - provenance  → source info (document, page, etc.)
    """
    id: str
    content: str
    block_type: str = "evidence"
    quality: float = 1.0
    tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    compressed: bool = False
    _original_tokens: int = 0  # before compression

    def __post_init__(self):
        if not self.tokens:
            self.tokens = _estimate_tokens(self.content)
        if not self._original_tokens:
            self._original_tokens = self.tokens

    def to_llamaindex_node(self) -> Dict[str, Any]:
        """Export as LlamaIndex-compatible dict."""
        return {
            "id": self.id,
            "text": self.content,
            "metadata": {
                **self.metadata,
                **self.provenance,
                "_tokenpak_quality": self.quality,
                "_tokenpak_compressed": self.compressed,
                "_tokenpak_tokens": self.tokens,
            },
        }

    def to_tokenpak_dict(self) -> Dict[str, Any]:
        """Export as TokenPak wire format."""
        return {
            "type": self.block_type,
            "id": self.id,
            "content": self.content,
            "quality": self.quality,
            "tokens": self.tokens,
            "metadata": self.metadata,
            "provenance": self.provenance,
            "compressed": self.compressed,
        }


# Also export as Node for backward compatibility
Node = LlamaBlock


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Estimate token count (1 token ≈ 4 chars). Fast, no imports."""
    return max(1, len(text) // 4)


def _make_node_id(text: str, metadata: Dict[str, Any]) -> str:
    """Stable ID from content hash."""
    source = metadata.get("file_name") or metadata.get("source") or text[:64]
    digest = hashlib.sha256(f"{source}:{text[:128]}".encode()).hexdigest()[:12]
    return f"node_{digest}"


# ---------------------------------------------------------------------------
# LlamaIndex Node → LlamaBlock
# ---------------------------------------------------------------------------

def llamaindex_node_to_block(
    node: Any,
    block_type: str = "evidence",
    block_id: Optional[str] = None,
) -> LlamaBlock:
    """
    Convert a LlamaIndex node to a LlamaBlock.

    Accepts:
      - dict: {text, id, metadata, score?, node_type?}
      - llama_index TextNode / NodeWithScore objects (when installed)
    """
    # --- Handle NodeWithScore wrapper ---
    score = 1.0
    if hasattr(node, "score") and hasattr(node, "node"):
        score = float(node.score or 1.0)
        node = node.node

    # --- Extract fields ---
    if isinstance(node, dict):
        text = node.get("text") or node.get("content") or ""
        metadata = dict(node.get("metadata", {}))
        node_id = block_id or node.get("id") or _make_node_id(text, metadata)
        score = node.get("score", score)
    else:
        # Real llama_index objects
        text = getattr(node, "text", None) or getattr(node, "get_content", lambda: "")()
        metadata = dict(getattr(node, "metadata", {}) or {})
        node_id = block_id or getattr(node, "node_id", None) or _make_node_id(text, metadata)

    # --- Build provenance ---
    provenance: Dict[str, Any] = {
        "source_type": "llamaindex",
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for key in ("file_name", "file_path", "url", "source", "doc_id", "page_label"):
        if key in metadata:
            provenance[key] = metadata.pop(key)

    return LlamaBlock(
        id=str(node_id),
        content=str(text),
        block_type=block_type,
        quality=float(score),
        metadata=metadata,
        provenance=provenance,
    )


def block_to_llamaindex_node(block: LlamaBlock, **extra_metadata) -> Dict[str, Any]:
    """Convert a LlamaBlock back to LlamaIndex node dict format."""
    metadata = {
        **block.metadata,
        **block.provenance,
        **extra_metadata,
        "_tokenpak_quality": block.quality,
        "_tokenpak_compressed": block.compressed,
    }
    return {
        "id": block.id,
        "text": block.content,
        "metadata": metadata,
    }


def llamaindex_nodes_to_blocks(
    nodes: List[Any],
    block_type: str = "evidence",
) -> List[LlamaBlock]:
    """Batch convert LlamaIndex nodes to LlamaBlocks."""
    return [llamaindex_node_to_block(n, block_type=block_type) for n in nodes]


def blocks_to_llamaindex_nodes(blocks: List[LlamaBlock]) -> List[Dict[str, Any]]:
    """Batch convert LlamaBlocks to LlamaIndex node dicts."""
    return [block_to_llamaindex_node(b) for b in blocks]
