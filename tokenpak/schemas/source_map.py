# SPDX-License-Identifier: MIT
"""Source map schema for TokenPak truth resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SourceMapSchema:
    """Source map: truth preference and conflict resolution."""

    repo_id: str
    """Repository identifier."""

    session_id: str
    """Session identifier."""

    truth_preference: str
    """Where to prioritize truth: 'repo' or 'artifact'."""

    bindings: Dict[str, str] = field(default_factory=dict)
    """Path -> artifact_id mappings (file written back to repo)."""

    conflicts: Dict[str, List[str]] = field(default_factory=dict)
    """Conflict log: path -> [artifact_ids] for auditing."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Resolution strategy metadata."""

    def resolve(self, path: str, artifact_id: Optional[str] = None) -> str:
        """
        Resolve where truth should come from.

        Returns 'repo' or 'artifact'.
        """
        # If artifact is bound to path AND artifact_id matches, prefer it
        if path in self.bindings and self.bindings[path] == artifact_id:
            return "artifact"

        # Otherwise use default preference
        return self.truth_preference

    def bind_artifact(self, path: str, artifact_id: str) -> None:
        """Mark artifact as written back to repo path."""
        self.bindings[path] = artifact_id

    def record_conflict(self, path: str, artifact_id: str) -> None:
        """Record a conflict for auditing."""
        if path not in self.conflicts:
            self.conflicts[path] = []
        self.conflicts[path].append(artifact_id)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "repo_id": self.repo_id,
            "session_id": self.session_id,
            "truth_preference": self.truth_preference,
            "bindings": self.bindings,
            "conflicts": self.conflicts,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceMapSchema:
        """Create from dict."""
        return cls(**data)
