from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Block:
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    priority: float = 1.0
    source: Optional[str] = None
    token_count: Optional[int] = None

    def to_dict(self):
        return {
            "content": self.content,
            "metadata": self.metadata,
            "priority": self.priority,
            "source": self.source,
        }


def doc_to_block(doc: Any) -> Block:
    metadata = getattr(doc, "metadata", {}) or {}
    priority = float(metadata.get("score", metadata.get("relevance_score", 1.0)))
    return Block(
        content=getattr(doc, "page_content", str(doc)),
        metadata=metadata,
        priority=priority,
        source=metadata.get("source"),
    )


def block_to_doc(block: Block) -> Any:
    class SimpleDoc:
        def __init__(self, content, meta):
            self.page_content = content
            self.metadata = meta

    meta = dict(block.metadata)
    if block.source:
        meta["source"] = block.source
    meta["tokenpak_priority"] = block.priority
    return SimpleDoc(block.content, meta)


def docs_to_blocks(docs: List[Any]) -> List[Block]:
    return [doc_to_block(d) for d in docs]


def blocks_to_docs(blocks: List[Block]) -> List[Any]:
    return [block_to_doc(b) for b in blocks]
