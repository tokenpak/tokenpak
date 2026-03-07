"""
Node ↔ TokenPak Block conversion utilities for LlamaIndex.

Converts between LlamaIndex Node format and TokenPak Block format.
"""

from typing import Optional, Any, Dict, List
from dataclasses import dataclass


@dataclass
class Node:
    """Minimal Node representation for TokenPak."""
    id: str
    content: str
    metadata: Dict[str, Any]
    node_type: str = "text"


def llamaindex_node_to_block(node: Dict[str, Any], block_id: Optional[str] = None):
    """Convert LlamaIndex Node to TokenPak Block."""
    content = node.get("text") or node.get("content") or ""
    metadata = node.get("metadata", {})
    
    if not block_id:
        block_id = node.get("id", f"node_{hash(content)}")
    
    return Node(
        id=block_id,
        content=content,
        metadata=metadata,
        node_type=node.get("node_type", "text"),
    )


def block_to_llamaindex_node(block: Node, **extra_metadata) -> Dict[str, Any]:
    """Convert TokenPak Block back to LlamaIndex Node format."""
    metadata = {**block.metadata, **extra_metadata}
    
    return {
        "id": block.id,
        "text": block.content,
        "metadata": metadata,
        "node_type": block.node_type,
    }


def llamaindex_nodes_to_blocks(nodes: List[Dict[str, Any]]) -> List[Node]:
    """Batch convert LlamaIndex Nodes to TokenPak Blocks."""
    return [llamaindex_node_to_block(node) for node in nodes]


def blocks_to_llamaindex_nodes(blocks: List[Node]) -> List[Dict[str, Any]]:
    """Batch convert TokenPak Blocks to LlamaIndex Nodes."""
    return [block_to_llamaindex_node(block) for block in blocks]
