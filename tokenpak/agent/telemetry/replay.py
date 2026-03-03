"""TokenPak Agent Telemetry Replay Store — Phase 1 stub.

Capture request/response metadata so sessions can be replayed with
a different model or settings (tokenpak replay list/show/<id>).

THIS IS A STUB. Full implementation arrives in Phase 1 (task 1.9).
The interface is defined here so Phase 0 modules can import it safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class ReplayEntry:
    """Metadata snapshot of a single proxied request for replay."""
    replay_id: str
    timestamp: datetime
    provider: str
    model: str
    input_tokens_raw: int
    input_tokens_sent: int
    tokens_saved: int
    # Full content intentionally omitted — see Phase 1 for content capture
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "replay_id": self.replay_id,
            "timestamp": self.timestamp.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "input_tokens_raw": self.input_tokens_raw,
            "input_tokens_sent": self.input_tokens_sent,
            "tokens_saved": self.tokens_saved,
            "metadata": self.metadata,
        }


class ReplayStore:
    """STUB: Capture and retrieve replay entries.

    Phase 1 will implement:
    - SQLite persistence
    - Content capture (opt-in, redactable)
    - Model substitution on replay
    - Diff rendering
    """

    def capture(self, entry: ReplayEntry) -> None:
        """Capture a replay entry. No-op in stub."""
        pass

    def list(self, limit: int = 20) -> list[ReplayEntry]:
        """List recent replay entries. Returns empty list in stub."""
        return []

    def get(self, replay_id: str) -> Optional[ReplayEntry]:
        """Retrieve a single replay entry. Returns None in stub."""
        return None


_store: Optional[ReplayStore] = None


def get_replay_store() -> ReplayStore:
    """Return the process-level singleton replay store."""
    global _store
    if _store is None:
        _store = ReplayStore()
    return _store
