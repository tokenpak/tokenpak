"""Context recipe engine for deterministic capsule assembly.

Adapted from TokenPak recipe_engine.py. No external package references.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import logging

import yaml

logger = logging.getLogger(__name__)


class MissingBlockError(KeyError):
    """Raised when required blocks are missing from available_blocks."""


@dataclass(frozen=True)
class Recipe:
    intent: str
    description: str
    required_blocks: tuple[str, ...]
    optional_blocks: tuple[str, ...]
    max_tokens: int
    priority_order: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str) -> "Recipe":
        if not isinstance(data, dict):
            raise ValueError(f"Recipe in {source} must be a mapping")

        required_fields = [
            "intent", "description", "required_blocks",
            "optional_blocks", "max_tokens", "priority_order",
        ]
        for f in required_fields:
            if f not in data:
                raise ValueError(f"Recipe in {source} missing field: {f}")

        intent = data["intent"]
        description = data["description"]
        required_blocks = data["required_blocks"]
        optional_blocks = data["optional_blocks"]
        max_tokens = data["max_tokens"]
        priority_order = data["priority_order"]

        if not isinstance(intent, str) or not intent.strip():
            raise ValueError(f"Recipe in {source} has invalid intent")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Recipe in {source} has invalid description")
        if not isinstance(required_blocks, list) or not all(isinstance(b, str) for b in required_blocks):
            raise ValueError(f"Recipe in {source} required_blocks must be list[str]")
        if not isinstance(optional_blocks, list) or not all(isinstance(b, str) for b in optional_blocks):
            raise ValueError(f"Recipe in {source} optional_blocks must be list[str]")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError(f"Recipe in {source} max_tokens must be positive int")
        if not isinstance(priority_order, list) or not all(isinstance(p, str) for p in priority_order):
            raise ValueError(f"Recipe in {source} priority_order must be list[str]")

        return cls(
            intent=intent.strip(),
            description=description.strip(),
            required_blocks=tuple(required_blocks),
            optional_blocks=tuple(optional_blocks),
            max_tokens=max_tokens,
            priority_order=tuple(priority_order),
        )


class RecipeEngine:
    """Loads and resolves intent recipes for deterministic context assembly."""

    def __init__(self) -> None:
        self._recipes: dict[str, Recipe] = {}

    def load_recipes(self, path: str) -> None:
        root = Path(path)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Recipe path not found: {path}")

        recipe_files = sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml")))
        if not recipe_files:
            raise ValueError(f"No recipe files found in {path}")

        for recipe_file in recipe_files:
            with recipe_file.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
            if data is None:
                raise ValueError(f"Recipe file {recipe_file} is empty")

            recipe = Recipe.from_dict(data, source=str(recipe_file))
            if recipe.intent in self._recipes:
                raise ValueError(f"Duplicate recipe intent: {recipe.intent}")
            self._recipes[recipe.intent] = recipe

    def get_recipe(self, intent: str) -> Recipe | None:
        return self._recipes.get(intent)

    def list_recipes(self) -> list[str]:
        return sorted(self._recipes.keys())

    def to_segments(
        self,
        recipe: Recipe,
        available_blocks: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        missing = [b for b in recipe.required_blocks if b not in available_blocks]
        if missing:
            raise MissingBlockError(
                f"Missing required blocks for intent '{recipe.intent}': {', '.join(missing)}"
            )

        segments: list[dict[str, Any]] = []
        current_tokens = 0
        order_counter = 0

        def estimate_tokens(text: str) -> int:
            return len(text) // 4

        def block_to_segment(block_id: str, block: Any, order: int) -> dict[str, Any]:
            if isinstance(block, dict):
                content = block.get("content", "")
                return {
                    "segment_id": block.get("segment_id", block_id),
                    "content": content,
                    "relevance_score": block.get("relevance_score", 0.5),
                    "segment_type": block.get("segment_type", "other"),
                    "order": block.get("order", order),
                }
            if isinstance(block, str):
                return {
                    "segment_id": block_id,
                    "content": block,
                    "relevance_score": 0.5,
                    "segment_type": "other",
                    "order": order,
                }
            if hasattr(block, "content"):
                return {
                    "segment_id": getattr(block, "segment_id", block_id),
                    "content": getattr(block, "content"),
                    "relevance_score": getattr(block, "relevance_score", 0.5),
                    "segment_type": getattr(block, "segment_type", "other"),
                    "order": getattr(block, "order", order),
                }
            return {
                "segment_id": block_id,
                "content": str(block),
                "relevance_score": 0.5,
                "segment_type": "other",
                "order": order,
            }

        def add_segment(block_id: str, block: Any, *, force: bool = False) -> bool:
            nonlocal current_tokens, order_counter
            segment = block_to_segment(block_id, block, order_counter)
            tokens = estimate_tokens(segment.get("content", ""))
            if force or current_tokens + tokens <= recipe.max_tokens:
                segments.append(segment)
                current_tokens += tokens
                order_counter += 1
                return True
            return False

        for block_id in recipe.required_blocks:
            add_segment(block_id, available_blocks[block_id], force=True)

        optional_blocks = list(recipe.optional_blocks)
        if "optional_by_relevance" in recipe.priority_order:
            optional_blocks.sort(
                key=lambda bid: (
                    -float(
                        available_blocks.get(bid, {}).get("relevance_score", 0.5)
                        if isinstance(available_blocks.get(bid, {}), dict)
                        else 0.5
                    )
                )
            )

        for block_id in optional_blocks:
            if block_id not in available_blocks:
                logger.info("Recipe %s missing optional block %s", recipe.intent, block_id)
                continue
            if not add_segment(block_id, available_blocks[block_id]):
                logger.info("Recipe %s skipped optional block %s (budget)", recipe.intent, block_id)

        return segments
