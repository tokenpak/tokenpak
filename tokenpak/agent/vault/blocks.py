"""TokenPak Agent Vault Block Storage — JSON-format block persistence."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class BlockRecord:
    """A compressed content block stored on disk."""

    block_id: str  # Typically path#hash[:8]
    path: str  # Source file path
    content_hash: str  # SHA256 of original content
    file_type: str  # "code" | "text" | "data"
    raw_tokens: int
    compressed_tokens: int
    compressed_content: str
    quality_score: float = 1.0
    indexed_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        if self.raw_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.raw_tokens

    @property
    def tokens_saved(self) -> int:
        return max(0, self.raw_tokens - self.compressed_tokens)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["compression_ratio"] = self.compression_ratio
        d["tokens_saved"] = self.tokens_saved
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "BlockRecord":
        # Drop derived fields that aren't constructor params
        data = {k: v for k, v in data.items() if k not in ("compression_ratio", "tokens_saved")}
        return cls(**data)


class BlockStore:
    """JSON-backed block storage for compressed file content.

    Each collection is stored as a single JSON file (suitable for small-medium
    vaults). For large vaults, Phase 1 introduces SQLite persistence.

    Usage::

        store = BlockStore("~/.tokenpak/blocks.json")
        store.save(record)
        block = store.get("path/to/file.py#abc123")
        results = store.search("token compression", top_k=5)
        store.flush()
    """

    def __init__(self, store_path: str = ":memory:"):
        self._path = store_path
        self._blocks: dict[str, BlockRecord] = {}

        if store_path != ":memory:":
            self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, record: BlockRecord) -> None:
        """Upsert a block record."""
        self._blocks[record.block_id] = record
        if self._path != ":memory:":
            self.flush()

    def get(self, block_id: str) -> Optional[BlockRecord]:
        return self._blocks.get(block_id)

    def get_by_path(self, path: str) -> list[BlockRecord]:
        return [b for b in self._blocks.values() if b.path == path]

    def delete(self, block_id: str) -> bool:
        if block_id in self._blocks:
            del self._blocks[block_id]
            if self._path != ":memory:":
                self.flush()
            return True
        return False

    def all(self) -> list[BlockRecord]:
        return list(self._blocks.values())

    def search(self, query: str, top_k: int = 10) -> list[BlockRecord]:
        """Naive keyword search over compressed content. Phase 1 adds embeddings."""
        q = query.lower()
        scored = []
        for block in self._blocks.values():
            score = block.compressed_content.lower().count(q)
            if score > 0:
                scored.append((score, block))
        scored.sort(key=lambda x: -x[0])
        return [b for _, b in scored[:top_k]]

    def stats(self) -> dict[str, Any]:
        blocks = list(self._blocks.values())
        total_raw = sum(b.raw_tokens for b in blocks)
        total_compressed = sum(b.compressed_tokens for b in blocks)
        return {
            "total_blocks": len(blocks),
            "total_raw_tokens": total_raw,
            "total_compressed_tokens": total_compressed,
            "total_tokens_saved": total_raw - total_compressed,
            "avg_compression_ratio": (
                sum(b.compression_ratio for b in blocks) / len(blocks) if blocks else 1.0
            ),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Write blocks to the JSON store file."""
        path = Path(self._path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {bid: block.to_dict() for bid, block in self._blocks.items()}
        path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        path = Path(self._path).expanduser()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._blocks = {bid: BlockRecord.from_dict(bd) for bid, bd in data.items()}
        except (json.JSONDecodeError, KeyError, TypeError):
            self._blocks = {}

    def __len__(self) -> int:
        return len(self._blocks)


_store: Optional[BlockStore] = None


def get_block_store(store_path: str = ":memory:") -> BlockStore:
    """Return the process-level singleton block store."""
    global _store
    if _store is None:
        _store = BlockStore(store_path)
    return _store
