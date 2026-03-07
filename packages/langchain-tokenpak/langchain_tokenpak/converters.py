"""
Document ↔ TokenPak Block conversion utilities for LangChain.

Converts between LangChain's Document format and TokenPak's Block format
for seamless integration in RAG pipelines.
"""

from typing import Optional, Any, Dict, List
from dataclasses import dataclass, asdict


@dataclass
class Block:
    """Minimal Block representation for TokenPak."""
    id: str
    content: str
    metadata: Dict[str, Any]
    block_type: str = "document"
    encoding: str = "utf-8"


def langchain_document_to_block(doc: Dict[str, Any], block_id: Optional[str] = None) -> Block:
    """
    Convert a LangChain Document to a TokenPak Block.

    Args:
        doc: LangChain Document dict with 'page_content' and 'metadata'
        block_id: Optional custom block ID (auto-generated if not provided)

    Returns:
        TokenPak Block
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
    Convert a TokenPak Block back to LangChain Document format.

    Args:
        block: TokenPak Block
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
    """Batch convert LangChain Documents to TokenPak Blocks."""
    return [langchain_document_to_block(doc) for doc in docs]


def blocks_to_langchain_documents(blocks: List[Block]) -> List[Dict[str, Any]]:
    """Batch convert TokenPak Blocks to LangChain Documents."""
    return [block_to_langchain_document(block) for block in blocks]
