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


# ─────────────────────────────────────────────────────────────────────────────
# Compression Recipe System (OSS tier)
# ─────────────────────────────────────────────────────────────────────────────

_OSS_RECIPES_DIR = Path(__file__).parent.parent.parent.parent / "recipes" / "oss"


@dataclass(frozen=True)
class CompressionRecipe:
    """A declarative compression recipe loaded from YAML."""

    name: str
    category: str
    description: str
    pattern: dict
    action: dict

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str) -> "CompressionRecipe":
        if not isinstance(data, dict):
            raise ValueError(f"CompressionRecipe in {source} must be a mapping")
        for field in ("name", "category", "description", "pattern", "action"):
            if field not in data:
                raise ValueError(f"CompressionRecipe in {source} missing field: {field}")
        name = str(data["name"]).strip()
        category = str(data["category"]).strip()
        if not name:
            raise ValueError(f"CompressionRecipe in {source} has empty name")
        if not category:
            raise ValueError(f"CompressionRecipe in {source} has empty category")
        return cls(
            name=name,
            category=category,
            description=str(data["description"]).strip(),
            pattern=dict(data["pattern"]),
            action=dict(data["action"]),
        )

    @property
    def compression_hint(self) -> float:
        """Expected compression ratio 0.0–1.0 (fraction of content removed)."""
        return float(self.action.get("compression_hint", 0.0))

    @property
    def operations(self) -> list[dict[str, Any]]:
        return list(self.action.get("operations", []))

    @property
    def match_mode(self) -> str:
        return str(self.pattern.get("match", "any"))

    def matches(self, filename: str = "", content_sample: str = "") -> bool:
        """Return True if this recipe is applicable to the given file/content."""
        mode = self.match_mode
        if mode == "any":
            return True
        if mode == "extension":
            exts = self.pattern.get("extensions", [])
            for ext in exts:
                if filename.endswith(ext):
                    return True
            return False
        if mode == "filename":
            fnames = self.pattern.get("filenames", [])
            base = Path(filename).name
            return base in fnames
        if mode == "content":
            keywords = self.pattern.get("keywords", [])
            return any(kw in content_sample for kw in keywords)
        if mode == "path_pattern":
            import re
            path_patterns = self.pattern.get("path_patterns", [])
            return any(re.search(p, filename) for p in path_patterns)
        # Unknown mode: conservative — skip
        return False


class CompressionRecipeEngine:
    """Loads and indexes OSS compression recipes from YAML files."""

    def __init__(self) -> None:
        self._recipes: dict[str, CompressionRecipe] = {}
        self._loaded = False

    def load_from_dir(self, path: str | Path | None = None) -> None:
        """Load all YAML recipe files from *path* (defaults to bundled OSS dir)."""
        root = Path(path) if path is not None else _OSS_RECIPES_DIR
        if not root.exists() or not root.is_dir():
            raise ValueError(f"CompressionRecipe path not found: {root}")

        recipe_files = sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml")))
        loaded = 0
        for recipe_file in recipe_files:
            with recipe_file.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if data is None:
                logger.warning("Empty recipe file: %s", recipe_file)
                continue
            try:
                recipe = CompressionRecipe.from_dict(data, source=str(recipe_file))
            except (ValueError, TypeError) as exc:
                logger.error("Failed to load recipe %s: %s", recipe_file, exc)
                continue
            if recipe.name in self._recipes:
                logger.warning("Duplicate recipe name %r — skipping %s", recipe.name, recipe_file)
                continue
            self._recipes[recipe.name] = recipe
            loaded += 1

        self._loaded = True
        logger.info("Loaded %d compression recipes from %s", loaded, root)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_from_dir()

    def get_recipe(self, name: str) -> CompressionRecipe | None:
        self._ensure_loaded()
        return self._recipes.get(name)

    def list_recipes(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._recipes.keys())

    def recipes_for_file(self, filename: str, content_sample: str = "") -> list[CompressionRecipe]:
        """Return recipes applicable to a given file, sorted by compression_hint desc."""
        self._ensure_loaded()
        applicable = [
            r for r in self._recipes.values()
            if r.matches(filename=filename, content_sample=content_sample)
        ]
        return sorted(applicable, key=lambda r: r.compression_hint, reverse=True)

    def by_category(self, category: str) -> list[CompressionRecipe]:
        self._ensure_loaded()
        return sorted(
            [r for r in self._recipes.values() if r.category == category],
            key=lambda r: r.name,
        )

    def categories(self) -> list[str]:
        self._ensure_loaded()
        return sorted({r.category for r in self._recipes.values()})

    def summary(self) -> dict[str, Any]:
        """Return a summary dict suitable for CLI display."""
        self._ensure_loaded()
        cats = self.categories()
        return {
            "total": len(self._recipes),
            "categories": {cat: len(self.by_category(cat)) for cat in cats},
        }


# Module-level singleton (lazy-loaded)
_oss_engine: CompressionRecipeEngine | None = None


def get_oss_engine() -> CompressionRecipeEngine:
    """Return the module-level CompressionRecipeEngine, loading recipes on first call."""
    global _oss_engine
    if _oss_engine is None:
        _oss_engine = CompressionRecipeEngine()
        _oss_engine.load_from_dir()
    return _oss_engine
