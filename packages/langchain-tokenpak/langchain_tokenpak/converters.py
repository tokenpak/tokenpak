"""
Document ↔ Pak block conversion utilities for LangChain.

Converts between LangChain's Document format and the Pak block format
for seamless integration in RAG pipelines.
"""

from typing import Optional, Any, Dict, List
from dataclasses import dataclass, asdict


@dataclass
class Block:
    """Minimal Pak block representation."""
    id: str
    content: str
    metadata: Dict[str, Any]
    block_type: str = "document"
    encoding: str = "utf-8"


def langchain_document_to_block(doc: Dict[str, Any], block_id: Optional[str] = None) -> Block:
    """
    Convert a LangChain Document to a Pak block.

    Args:
        doc: LangChain Document dict with 'page_content' and 'metadata'
        block_id: Optional custom block ID (auto-generated if not provided)

    Returns:
        Pak block
    """
    page_content = doc.get("page_content", "")
    metadata = doc.get("metadata", {})
    
    if not block_id:
        import hashlib
        block_id = f"doc_{hashlib.md5(page_content.encode()).hexdigest()[:12]}"
    
    return Block(
        id=block_id,
        content=page_content,
        metadata=metadata,
        block_type="document",
    )


def block_to_langchain_document(block: Block, **extra_metadata) -> Dict[str, Any]:
    """
    Convert a Pak block back to LangChain Document format.

    Args:
        block: Pak block
        extra_metadata: Additional metadata to merge

    Returns:
        LangChain Document dict
    """
    metadata = {**block.metadata, **extra_metadata}
    metadata["block_id"] = block.id
    metadata["block_type"] = block.block_type
    
    return {
        "page_content": block.content,
        "metadata": metadata,
    }


def langchain_documents_to_blocks(docs: List[Dict[str, Any]]) -> List[Block]:
    """Batch convert LangChain Documents to Pak blocks."""
    return [langchain_document_to_block(doc) for doc in docs]


def blocks_to_langchain_documents(blocks: List[Block]) -> List[Dict[str, Any]]:
    """Batch convert Pak blocks to LangChain Documents."""
    return [block_to_langchain_document(block) for block in blocks]
