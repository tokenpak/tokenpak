"""TokenPak Team Shared Vault (5.5)

Shared context index across team. Agents can contribute and query blocks.
Merge strategy: team blocks + local blocks; local blocks take priority
(i.e., a local block at the same path overrides the team block).

CLI surface:
    tokenpak vault push <path>   — contribute local blocks to shared vault
    tokenpak vault pull          — sync shared vault blocks locally

Storage: JSON file (suitable for small-medium teams; path shared via config
or TOKENPAK_TEAM_VAULT env var).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SharedVaultBlock:
    """A block contributed to the shared team vault."""

    block_id: str  # "<agent>:<path>#<hash[:8]>"
    contributor: str  # agent name that contributed this block
    path: str  # source file path (relative or full)
    content_hash: str  # SHA256 of original content
    file_type: str  # "code" | "text" | "data"
    raw_tokens: int
    compressed_tokens: int
    compressed_content: str
    quality_score: float = 1.0
    contributed_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def compression_ratio(self) -> float:
        if self.raw_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.raw_tokens

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["compression_ratio"] = round(self.compression_ratio, 3)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SharedVaultBlock":
        data = {k: v for k, v in data.items() if k != "compression_ratio"}
        return cls(**data)


class SharedVault:
    """JSON-backed shared vault for team context blocks.

    Merge strategy (team blocks lower priority than local)::

        merged = merge_with_local(local_blocks)
        # local_blocks override team blocks at the same path

    Usage::

        vault = SharedVault("~/.tokenpak/team/shared_vault.json")
        vault.push_block(block)
        blocks = vault.pull_blocks()
        merged = vault.merge_with_local(local_blocks)
    """

    def __init__(self, store_path: str = ":memory:") -> None:
        self._path = store_path
        self._blocks: Dict[str, SharedVaultBlock] = {}
        self._lock = threading.Lock()

        if store_path != ":memory:":
            self._load()

    # ------------------------------------------------------------------
    # Push / pull
    # ------------------------------------------------------------------

    def push_block(self, block: SharedVaultBlock) -> None:
        """Add or update a block in the shared vault."""
        with self._lock:
            self._blocks[block.block_id] = block
            self._persist()

    def push_blocks(self, blocks: List[SharedVaultBlock]) -> int:
        """Bulk push; returns count of blocks stored."""
        with self._lock:
            for block in blocks:
                self._blocks[block.block_id] = block
            self._persist()
        return len(blocks)

    def pull_blocks(self, contributor: Optional[str] = None) -> List[SharedVaultBlock]:
        """Return all blocks (or only from a specific contributor)."""
        with self._lock:
            if contributor:
                return [b for b in self._blocks.values() if b.contributor == contributor]
            return list(self._blocks.values())

    def get_block(self, block_id: str) -> Optional[SharedVaultBlock]:
        with self._lock:
            return self._blocks.get(block_id)

    def delete_block(self, block_id: str) -> bool:
        with self._lock:
            if block_id not in self._blocks:
                return False
            del self._blocks[block_id]
            self._persist()
            return True

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_with_local(self, local_blocks: List[Any]) -> List[Any]:
        """Merge team blocks with local blocks.

        Local blocks take priority: if a local block covers the same path
        as a team block, the local block wins.

        Args:
            local_blocks: list of local BlockRecord objects (must have .path)

        Returns:
            merged list — local blocks first, then team blocks that have no
            local equivalent.
        """
        with self._lock:
            team_blocks = list(self._blocks.values())

        local_paths = {b.path for b in local_blocks}
        team_only = [b for b in team_blocks if b.path not in local_paths]

        return list(local_blocks) + team_only

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 10) -> List[SharedVaultBlock]:
        """Naive keyword search over compressed content."""
        q = query.lower()
        with self._lock:
            scored = []
            for block in self._blocks.values():
                score = block.compressed_content.lower().count(q)
                if score > 0:
                    scored.append((score, block))
        scored.sort(key=lambda x: -x[0])
        return [b for _, b in scored[:top_k]]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            blocks = list(self._blocks.values())
        total_raw = sum(b.raw_tokens for b in blocks)
        total_compressed = sum(b.compressed_tokens for b in blocks)
        contributors = list({b.contributor for b in blocks})
        return {
            "total_blocks": len(blocks),
            "contributors": contributors,
            "total_raw_tokens": total_raw,
            "total_compressed_tokens": total_compressed,
            "tokens_saved": total_raw - total_compressed,
            "avg_compression_ratio": (
                sum(b.compression_ratio for b in blocks) / len(blocks) if blocks else 1.0
            ),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        if self._path == ":memory:":
            return
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
            self._blocks = {bid: SharedVaultBlock.from_dict(bd) for bid, bd in data.items()}
        except (json.JSONDecodeError, KeyError, TypeError):
            self._blocks = {}

    def __len__(self) -> int:
        return len(self._blocks)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_shared_vault: Optional[SharedVault] = None
_vault_lock = threading.Lock()


def get_shared_vault(store_path: str = ":memory:") -> SharedVault:
    """Return the process-level singleton shared vault."""
    global _shared_vault
    with _vault_lock:
        if _shared_vault is None:
            _shared_vault = SharedVault(store_path)
    return _shared_vault
