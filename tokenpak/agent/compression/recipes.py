"""Context recipe engine for deterministic capsule assembly.

Adapted from TokenPak recipe_engine.py. No external package references.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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
            "intent",
            "description",
            "required_blocks",
            "optional_blocks",
            "max_tokens",
            "priority_order",
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
        if not isinstance(required_blocks, list) or not all(
            isinstance(b, str) for b in required_blocks
        ):
            raise ValueError(f"Recipe in {source} required_blocks must be list[str]")
        if not isinstance(optional_blocks, list) or not all(
            isinstance(b, str) for b in optional_blocks
        ):
            raise ValueError(f"Recipe in {source} optional_blocks must be list[str]")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError(f"Recipe in {source} max_tokens must be positive int")
        if not isinstance(priority_order, list) or not all(
            isinstance(p, str) for p in priority_order
        ):
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
            r
            for r in self._recipes.values()
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7C — Deterministic Compression Rule Engine
# ─────────────────────────────────────────────────────────────────────────────
# This engine operates on ContentSegment objects (raw text + segment_type).
# It is intentionally named CompressionRuleEngine to avoid conflict with the
# YAML-recipe-based RecipeEngine above.  The public API matches the spec:
#   engine.select_recipes(segment) -> list[RecipeType]
#   engine.apply_recipes(segment, recipes) -> ContentSegment
# ─────────────────────────────────────────────────────────────────────────────

import dataclasses
import re
from enum import Enum
from typing import List

# ---------------------------------------------------------------------------
# Token counter (cheap, deterministic — matches rest of codebase)
# ---------------------------------------------------------------------------


def _count_tokens(text: str) -> int:
    """Approximate token count: 1 token ≈ 4 characters."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# RecipeType
# ---------------------------------------------------------------------------


class RecipeType(Enum):
    WHITESPACE_COLLAPSE = "whitespace_collapse"
    LIST_DEDUP = "list_dedup"
    PHRASE_SUBSTITUTION = "phrase_substitution"
    TRUNCATE_TAIL = "truncate_tail"
    BOILERPLATE_STRIP = "boilerplate_strip"


# ---------------------------------------------------------------------------
# ContentSegment — text-bearing segment used by CompressionRuleEngine
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ContentSegment:
    """Lightweight segment that carries raw text and its classification."""

    raw_content: str
    segment_type: str  # uses SegmentType string values
    raw_tokens: int = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.raw_tokens = _count_tokens(self.raw_content)

    def with_content(self, new_content: str) -> "ContentSegment":
        """Return a new ContentSegment with updated content (and recounted tokens)."""
        seg = dataclasses.replace(self, raw_content=new_content)
        seg.raw_tokens = _count_tokens(new_content)
        return seg


# ---------------------------------------------------------------------------
# Phrase substitution map
# ---------------------------------------------------------------------------

PHRASE_MAP: dict[str, str] = {
    "the following": ":",
    "as mentioned above": "[ref]",
    "for more information": "[info]",
    "in this case": "here",
    "it is important to note that": "note:",
    "it should be noted that": "note:",
    "please be aware that": "note:",
    "in order to": "to",
    "at this point in time": "now",
    "due to the fact that": "because",
}


# ---------------------------------------------------------------------------
# CompressionRuleEngine
# ---------------------------------------------------------------------------

_TRUNCATE_TAIL_TOKEN_THRESHOLD = 2000
_TRUNCATE_TAIL_KEEP_RATIO = 0.80

# Boilerplate patterns (copyright/license, installation sections)
_BOILERPLATE_PATTERNS = [
    re.compile(r"^[ \t]*#.*?[Cc]opyright.*?$", re.MULTILINE),
    re.compile(r"^[ \t]*#.*?[Ll]icense.*?$", re.MULTILINE),
    re.compile(r"^[ \t]*//.*?[Cc]opyright.*?$", re.MULTILINE),
    re.compile(r"^[ \t]*//.*?[Ll]icense.*?$", re.MULTILINE),
    re.compile(
        r"^##\s+Installation\s*\n(?:.*\n)*?(?=^##|\Z)",
        re.MULTILINE,
    ),
    re.compile(r"^---\s*\n(?:copyright|license):.*?^---\s*\n", re.MULTILINE | re.DOTALL),
]

_API_RATE_LIMIT_PATTERN = re.compile(
    r"(?im)^.*?api\s+rate\s+limit.*?$",
    re.MULTILINE,
)


class CompressionRuleEngine:
    """Deterministic compression rule engine for ContentSegment objects.

    Applies text reduction rules in a fixed order:
    1. WHITESPACE_COLLAPSE
    2. LIST_DEDUP
    3. BOILERPLATE_STRIP
    4. TRUNCATE_TAIL
    5. PHRASE_SUBSTITUTION  (last — ensures phrases not re-introduced)

    Usage::

        engine = CompressionRuleEngine()
        recipes = engine.select_recipes(segment)
        compressed = engine.apply_recipes(segment, recipes)
    """

    def __init__(self) -> None:
        # Tracks how many times API-rate-limit text has been seen in this session
        self._api_rate_limit_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_recipes(self, segment: ContentSegment) -> List[RecipeType]:
        """Return ordered list of RecipeType that apply to *segment*."""
        recipes: List[RecipeType] = []
        seg_type = segment.segment_type

        if seg_type == "tool_output":
            recipes.append(RecipeType.WHITESPACE_COLLAPSE)
            if segment.raw_tokens > _TRUNCATE_TAIL_TOKEN_THRESHOLD:
                recipes.append(RecipeType.TRUNCATE_TAIL)

        if seg_type == "retrieval":
            recipes.append(RecipeType.LIST_DEDUP)
            recipes.append(RecipeType.PHRASE_SUBSTITUTION)

        if seg_type in ("memory", "assistant_context"):
            recipes.append(RecipeType.BOILERPLATE_STRIP)

        return recipes

    def apply_recipes(self, segment: ContentSegment, recipes: List[RecipeType]) -> ContentSegment:
        """Apply *recipes* in order; return new ContentSegment with updated tokens."""
        content = segment.raw_content
        for recipe in recipes:
            content = self._apply_rule(content, recipe)
        return segment.with_content(content)

    # ------------------------------------------------------------------
    # Internal dispatcher
    # ------------------------------------------------------------------

    def _apply_rule(self, content: str, recipe: RecipeType) -> str:
        if recipe == RecipeType.WHITESPACE_COLLAPSE:
            return self._whitespace_collapse(content)
        if recipe == RecipeType.LIST_DEDUP:
            return self._list_dedup(content)
        if recipe == RecipeType.PHRASE_SUBSTITUTION:
            return self._phrase_substitution(content)
        if recipe == RecipeType.TRUNCATE_TAIL:
            return self._truncate_tail(content)
        if recipe == RecipeType.BOILERPLATE_STRIP:
            return self._boilerplate_strip(content)
        return content  # unknown recipe — pass through

    # ------------------------------------------------------------------
    # Rule implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _whitespace_collapse(content: str) -> str:
        """Collapse excess whitespace without altering meaningful structure.

        - 3+ consecutive newlines → 2 newlines
        - 4+ consecutive spaces (not at line-start indentation) → 2 spaces
        - Trim trailing whitespace from every line
        Target: 5-10% reduction on verbose output.
        """
        # Trim trailing whitespace per line
        lines = [line.rstrip() for line in content.split("\n")]
        content = "\n".join(lines)
        # Collapse 3+ newlines to 2
        content = re.sub(r"\n{3,}", "\n\n", content)
        # Collapse 4+ mid-line spaces to 2 (negative lookbehind: not at line start)
        content = re.sub(r"(?m)(?<=\S) {4,}", "  ", content)
        return content

    @staticmethod
    def _list_dedup(content: str) -> str:
        """Remove duplicate bullet / numbered list items (case-insensitive).

        Preserves order of first occurrence.
        Target: 15-25% reduction on repeated content.
        """
        lines = content.split("\n")
        seen: set[str] = set()
        result: list[str] = []
        bullet_re = re.compile(r"^(\s*(?:[-*+]|\d+\.)\s+)(.*)")

        for line in lines:
            m = bullet_re.match(line)
            if m:
                item_text = m.group(2).strip().lower()
                if item_text in seen:
                    continue  # duplicate — drop it
                seen.add(item_text)
            result.append(line)

        return "\n".join(result)

    @staticmethod
    def _phrase_substitution(content: str) -> str:
        """Replace verbose phrases with compact equivalents (case-insensitive).

        Target: 3-5% reduction.
        """
        for phrase, replacement in PHRASE_MAP.items():
            # Case-insensitive word-boundary-aware replacement
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            content = pattern.sub(replacement, content)
        return content

    @staticmethod
    def _truncate_tail(content: str) -> str:
        """Drop the last 20% of oversized segments (> 2000 tokens).

        Appends a ``[...truncated...]`` marker.
        Target: 20% reduction on oversized segments.
        """
        tokens = _count_tokens(content)
        if tokens <= _TRUNCATE_TAIL_TOKEN_THRESHOLD:
            return content
        # Work in characters (token ≈ 4 chars)
        keep_chars = int(len(content) * _TRUNCATE_TAIL_KEEP_RATIO)
        return content[:keep_chars] + "\n[...truncated...]"

    def _boilerplate_strip(self, content: str) -> str:
        """Remove copyright headers, license blocks, and Installation sections.

        Also strips API rate-limit warnings on their 3rd+ occurrence in the session.
        Target: 2-5% reduction.
        """
        # Remove static boilerplate patterns
        for pattern in _BOILERPLATE_PATTERNS:
            content = pattern.sub("", content)

        # API rate-limit warnings: count occurrences, strip from 3rd onward
        def _rate_limit_replacer(m: re.Match) -> str:
            self._api_rate_limit_count += 1
            if self._api_rate_limit_count >= 3:
                return ""
            return m.group(0)

        content = _API_RATE_LIMIT_PATTERN.sub(_rate_limit_replacer, content)
        # Collapse any blank lines left behind by removals
        content = re.sub(r"\n{3,}", "\n\n", content)
        return content
