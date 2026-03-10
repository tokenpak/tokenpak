"""Predictive prefetching for TokenPak agentic workflows.

Learns workflow transition patterns and proactively preloads likely-needed
artifacts into cache.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Sequence

DEFAULT_DIAGNOSTIC_ARTIFACTS = [
    "logs/latest.log",
    "config/settings.yaml",
    "env/runtime.env",
]


@dataclass
class PrefetchStore:
    """Persistent state for predictive prefetching."""

    version: int = 1
    # completed_step -> next_step -> artifact -> count
    workflow_patterns: Dict[str, Dict[str, Dict[str, int]]] = field(default_factory=dict)
    # task_type -> artifact -> count
    task_type_artifacts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # error kind -> artifacts
    error_artifacts: Dict[str, List[str]] = field(default_factory=dict)


class PredictivePrefetcher:
    """Learns likely next artifacts and preloads them on trigger events."""

    def __init__(self, store_path: str | None = None) -> None:
        self.store_path = Path(store_path).expanduser() if store_path else None
        self.store = self._load() if self.store_path else PrefetchStore()

        # Baseline diagnostic defaults
        if "default" not in self.store.error_artifacts:
            self.store.error_artifacts["default"] = list(DEFAULT_DIAGNOSTIC_ARTIFACTS)

    def _load(self) -> PrefetchStore:
        assert self.store_path is not None
        if not self.store_path.exists():
            return PrefetchStore(error_artifacts={"default": list(DEFAULT_DIAGNOSTIC_ARTIFACTS)})

        try:
            raw = json.loads(self.store_path.read_text())
        except (json.JSONDecodeError, OSError):
            return PrefetchStore(error_artifacts={"default": list(DEFAULT_DIAGNOSTIC_ARTIFACTS)})

        return PrefetchStore(
            version=raw.get("version", 1),
            workflow_patterns=raw.get("workflow_patterns", {}),
            task_type_artifacts=raw.get("task_type_artifacts", {}),
            error_artifacts=raw.get("error_artifacts", {"default": list(DEFAULT_DIAGNOSTIC_ARTIFACTS)}),
        )

    def save(self) -> None:
        if not self.store_path:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(
                {
                    "version": self.store.version,
                    "workflow_patterns": self.store.workflow_patterns,
                    "task_type_artifacts": self.store.task_type_artifacts,
                    "error_artifacts": self.store.error_artifacts,
                },
                indent=2,
            )
        )

    def record_transition(self, completed_step: str, next_step: str, artifacts: Iterable[str]) -> None:
        """Learn artifact demand for transition: completed_step -> next_step."""
        completed_step = completed_step.strip()
        next_step = next_step.strip()
        if not completed_step or not next_step:
            return

        step_map = self.store.workflow_patterns.setdefault(completed_step, {})
        next_map = step_map.setdefault(next_step, {})
        for artifact in artifacts:
            artifact = artifact.strip()
            if artifact:
                next_map[artifact] = next_map.get(artifact, 0) + 1

    def learn_workflow_path(self, steps: Sequence[str], artifacts_by_step: Dict[str, Iterable[str]]) -> None:
        """Learn transition patterns from a historical workflow path."""
        for idx in range(len(steps) - 1):
            current_step = steps[idx]
            next_step = steps[idx + 1]
            self.record_transition(current_step, next_step, artifacts_by_step.get(next_step, []))

    def register_task_type_artifacts(self, task_type: str, artifacts: Iterable[str]) -> None:
        """Learn common files used by a task type."""
        task_type = task_type.strip().lower()
        if not task_type:
            return

        bucket = self.store.task_type_artifacts.setdefault(task_type, {})
        for artifact in artifacts:
            artifact = artifact.strip()
            if artifact:
                bucket[artifact] = bucket.get(artifact, 0) + 1

    def register_error_artifacts(self, error_kind: str, artifacts: Iterable[str]) -> None:
        """Register diagnostic artifacts to load when an error kind is detected."""
        error_kind = error_kind.strip().lower() or "default"
        merged = list(dict.fromkeys([*self.store.error_artifacts.get(error_kind, []), *list(artifacts)]))
        self.store.error_artifacts[error_kind] = merged

    def recommend_for_completed_step(self, completed_step: str, limit: int = 5) -> List[str]:
        """Predict likely artifacts needed after this completed step."""
        pattern = self.store.workflow_patterns.get(completed_step, {})
        if not pattern:
            return []

        scores: Counter[str] = Counter()
        for next_step_data in pattern.values():
            scores.update(next_step_data)

        return [artifact for artifact, _ in scores.most_common(limit)]

    def recommend_for_task_type(self, task_type: str, limit: int = 5) -> List[str]:
        task_type = task_type.strip().lower()
        artifact_counts = self.store.task_type_artifacts.get(task_type, {})
        if not artifact_counts:
            return []
        return [a for a, _ in Counter(artifact_counts).most_common(limit)]

    def recommend_for_error(self, error_kind: str, extra_artifacts: Iterable[str] | None = None) -> List[str]:
        error_kind = error_kind.strip().lower() or "default"
        defaults = self.store.error_artifacts.get("default", list(DEFAULT_DIAGNOSTIC_ARTIFACTS))
        specifics = self.store.error_artifacts.get(error_kind, [])
        extra = list(extra_artifacts or [])
        return list(dict.fromkeys([*defaults, *specifics, *extra]))

    def on_workflow_step_completed(
        self,
        completed_step: str,
        preload: Callable[[str], None],
        limit: int = 5,
    ) -> List[str]:
        candidates = self.recommend_for_completed_step(completed_step, limit=limit)
        for artifact in candidates:
            preload(artifact)
        return candidates

    def on_task_type_recognized(
        self,
        task_type: str,
        preload: Callable[[str], None],
        limit: int = 5,
    ) -> List[str]:
        candidates = self.recommend_for_task_type(task_type, limit=limit)
        for artifact in candidates:
            preload(artifact)
        return candidates

    def on_error_detected(
        self,
        error_kind: str,
        preload: Callable[[str], None],
        extra_artifacts: Iterable[str] | None = None,
    ) -> List[str]:
        candidates = self.recommend_for_error(error_kind, extra_artifacts=extra_artifacts)
        for artifact in candidates:
            preload(artifact)
        return candidates
