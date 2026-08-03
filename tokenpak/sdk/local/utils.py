"""
utils.py — Lightweight Block and TokenPak shims + helper utilities.

tokenpak-local uses its own minimal Block/TokenPak shims.
The tokenpak core package's Block is a registry object with a different
interface (file storage), so we always use these chat-focused shims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

_SDK_AVAILABLE = False


@dataclass
class Block:
    """Minimal TokenPak Block shim."""

    type: str = "evidence"
    content: str = ""
    quality: float = 1.0
    id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    tokens: int = 0

    def __post_init__(self):
        if not self.tokens:
            self.tokens = max(1, len(self.content) // 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "quality": self.quality,
            "id": self.id,
            "metadata": self.metadata,
            "provenance": self.provenance,
            "tokens": self.tokens,
        }


class TokenPak:
    """Minimal TokenPak shim that compiles to OpenAI-style messages."""

    def __init__(
        self,
        budget: Optional[int] = None,
        instructions: str = "",
    ):
        self.budget = budget
        self.instructions = instructions
        self._blocks: List[Block] = []

    def add(self, block: Block) -> "TokenPak":
        self._blocks.append(block)
        return self

    def to_messages(self) -> List[Dict[str, str]]:
        """Compile blocks to OpenAI chat messages list."""
        parts: List[str] = []
        if self.instructions:
            parts.append(self.instructions)
        for b in self._blocks:
            parts.append(f"[{b.type.upper()}]\n{b.content}")
        system_content = "\n\n".join(parts)
        return [{"role": "system", "content": system_content}]

    def compile(self) -> "TokenPak":
        return self

    @property
    def total_tokens(self) -> int:
        return sum(b.tokens for b in self._blocks)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def blocks_from_texts(
    texts: Sequence[str],
    block_type: str = "evidence",
    quality: float = 1.0,
    metadata_list: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Block]:
    """
    Convert a list of text strings to TokenPak Blocks.

    Args:
        texts:         Sequence of document texts.
        block_type:    Block type to assign (default "evidence").
        quality:       Quality score to assign (default 1.0).
        metadata_list: Optional per-text metadata dicts.

    Returns:
        List of Block objects.

    Example:
        docs = ["Context A...", "Context B..."]
        blocks = blocks_from_texts(docs, block_type="evidence")
    """
    result: List[Block] = []
    for i, text in enumerate(texts):
        meta = {}
        if metadata_list and i < len(metadata_list):
            meta = dict(metadata_list[i])
        block = Block(
            type=block_type,
            content=text,
            quality=quality,
            metadata=meta,
            provenance={
                "source_type": "text",
                "index": i,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        result.append(block)
    return result


def pack_from_blocks(
    blocks: Sequence[Block],
    instructions: str = "",
    budget: Optional[int] = None,
) -> "TokenPak":
    """
    Build a TokenPak from a list of blocks.

    Args:
        blocks:       Blocks to include.
        instructions: System instructions / task description.
        budget:       Optional token budget. If provided and SDK is available,
                      will be set on the pack's policy.

    Returns:
        A TokenPak instance ready for .to_messages() or .compile().
    """
    pack = TokenPak(budget=budget, instructions=instructions)
    for block in blocks:
        pack.add(block)
    return pack
