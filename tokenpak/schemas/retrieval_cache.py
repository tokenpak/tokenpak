# SPDX-License-Identifier: Apache-2.0
"""Retrieval cache schema for TokenPak."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


@dataclass
class RetrievalCacheSchema:
    """Retrieval cache entry with TTL and coverage tracking."""

    query_fingerprint: str
    """Normalized query fingerprint (for cache key)."""

    session_id: str
    """Session identifier."""

    repo_id: str
    """Repository identifier."""

    intent: str
    """Query intent (search, refactor, debug, etc.)."""

    results: List[Dict[str, Any]] = field(default_factory=list)
    """Cached retrieval results."""

    coverage_score: float = 0.0
    """Coverage quality (0.0-1.0)."""

    pack_plan: Optional[Dict[str, Any]] = None
    """Associated packing plan (optional)."""

    ttl_minutes: int = 20
    """Time-to-live in minutes."""

    created_at: datetime = field(default_factory=datetime.utcnow)
    """When cache entry was created."""

    last_used_at: datetime = field(default_factory=datetime.utcnow)
    """Last time cache was accessed."""

    use_count: int = 0
    """Number of times cache was hit."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional metadata."""

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        now = datetime.utcnow()
        ttl_delta = timedelta(minutes=self.ttl_minutes)
        return (now - self.created_at) > ttl_delta

    def touch(self) -> None:
        """Update last_used_at and increment use_count."""
        self.last_used_at = datetime.utcnow()
        self.use_count += 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "query_fingerprint": self.query_fingerprint,
            "session_id": self.session_id,
            "repo_id": self.repo_id,
            "intent": self.intent,
            "results": self.results,
            "coverage_score": self.coverage_score,
            "pack_plan": self.pack_plan,
            "ttl_minutes": self.ttl_minutes,
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat(),
            "use_count": self.use_count,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RetrievalCacheSchema:
        """Create from dict."""
        data = dict(data)  # Copy to avoid mutation
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("last_used_at"), str):
            data["last_used_at"] = datetime.fromisoformat(data["last_used_at"])
        return cls(**data)
