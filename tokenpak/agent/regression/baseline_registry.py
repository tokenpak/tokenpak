# SPDX-License-Identifier: Apache-2.0
"""Baseline registry for TokenPak regression detection."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class BaselineEntry:
    """Stored baseline for a workflow."""

    workflow_id: str
    artifact_hash: str
    artifact_snapshot: Dict[str, Any]
    output_shape: Dict[str, Any]
    validation_result: Dict[str, Any]
    created_at: str
    updated_at: str
    pass_count: int = 1

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BaselineEntry:
        """Create from dict."""
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return asdict(self)


class BaselineRegistry:
    """Store and manage workflow baselines."""

    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize baseline registry.

        Args:
            registry_path: Path to baselines.json (default: ~/.tokenpak/baselines.json)
        """
        if registry_path is None:
            registry_path = str(Path.home() / ".tokenpak" / "baselines.json")

        self.registry_path = registry_path
        Path(self.registry_path).parent.mkdir(parents=True, exist_ok=True)
        self._load_registry()

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if Path(self.registry_path).exists():
            with open(self.registry_path, "r") as f:
                data = json.load(f)
                self.baselines = {
                    k: BaselineEntry.from_dict(v) for k, v in data.items()
                }
        else:
            self.baselines = {}

    def _save_registry(self) -> None:
        """Save registry to disk."""
        with open(self.registry_path, "w") as f:
            data = {k: v.to_dict() for k, v in self.baselines.items()}
            json.dump(data, f, indent=2)

    def store_baseline(self, entry: BaselineEntry) -> None:
        """Store or update a baseline."""
        self.baselines[entry.workflow_id] = entry
        self._save_registry()

    def get_baseline(self, workflow_id: str) -> Optional[BaselineEntry]:
        """Retrieve a baseline."""
        return self.baselines.get(workflow_id)

    def delete_baseline(self, workflow_id: str) -> None:
        """Delete a baseline."""
        if workflow_id in self.baselines:
            del self.baselines[workflow_id]
            self._save_registry()

    def update_pass_count(self, workflow_id: str, increment: int = 1) -> None:
        """Increment pass count for a baseline."""
        entry = self.get_baseline(workflow_id)
        if entry:
            entry.pass_count += increment
            entry.updated_at = datetime.utcnow().isoformat()
            self._save_registry()

    def list_baselines(self) -> Dict[str, BaselineEntry]:
        """Get all baselines."""
        return dict(self.baselines)
