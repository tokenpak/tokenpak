# SPDX-License-Identifier: Apache-2.0
"""Artifact schema for TokenPak dynamic context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class ArtifactSchema:
    """Artifact schema: large content you don't want to resend."""

    id: str
    """Unique artifact identifier (sha256 of content)."""

    session_id: str
    """Session where artifact was created."""

    origin: str
    """Where artifact came from (code_dump, tool_output, diff, log, etc.)."""

    kind: str
    """Content kind (code, markdown, json, binary, etc.)."""

    content_ref: str
    """Reference to content (file path, url, or inline hash)."""

    repo_binding: Optional[str] = None
    """If artifact was written back to repo, path where it was written."""

    size_bytes: int = 0
    """Size of artifact content in bytes."""

    token_estimate: int = 0
    """Estimated tokens (for budget tracking)."""

    labels: Dict[str, Any] = field(default_factory=dict)
    """Metadata labels (e.g., language, framework, etc.)."""

    created_at: datetime = field(default_factory=datetime.utcnow)
    """When artifact was created."""

    accessed_at: datetime = field(default_factory=datetime.utcnow)
    """Last access time (for cache eviction)."""

    stats: Dict[str, Any] = field(default_factory=dict)
    """Statistics (retrieval_count, cache_hits, etc.)."""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "origin": self.origin,
            "kind": self.kind,
            "content_ref": self.content_ref,
            "repo_binding": self.repo_binding,
            "size_bytes": self.size_bytes,
            "token_estimate": self.token_estimate,
            "labels": self.labels,
            "created_at": self.created_at.isoformat(),
            "accessed_at": self.accessed_at.isoformat(),
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactSchema:
        """Create from dict."""
        data = dict(data)  # Copy to avoid mutation
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if isinstance(data.get("accessed_at"), str):
            data["accessed_at"] = datetime.fromisoformat(data["accessed_at"])
        return cls(**data)
