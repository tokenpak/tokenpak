"""Stage registry for the optimization pipeline."""

from __future__ import annotations

from typing import Dict, Iterator, List

from .stage import OptimizationStage


class StageRegistry:
    """Ordered registry of optimization stages.

    Insertion order is preserved (Python dict semantics). The pipeline runs
    stages in the order they were registered. Stage names must be unique;
    re-registering a name replaces the previous entry.
    """

    def __init__(self) -> None:
        self._stages: Dict[str, OptimizationStage] = {}

    def register(self, stage: OptimizationStage) -> None:
        if not getattr(stage, "name", None):
            raise ValueError("OptimizationStage requires a non-empty 'name'")
        self._stages[stage.name] = stage

    def unregister(self, name: str) -> None:
        self._stages.pop(name, None)

    def get(self, name: str) -> OptimizationStage:
        return self._stages[name]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._stages

    def __iter__(self) -> Iterator[OptimizationStage]:
        return iter(self._stages.values())

    def __len__(self) -> int:
        return len(self._stages)

    def names(self) -> List[str]:
        return list(self._stages.keys())

    def clear(self) -> None:
        self._stages.clear()
