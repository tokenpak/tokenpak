"""
SemanticResolver — Deterministic alias → canonical resolution.

Matching strategy (in priority order):
1. Exact full-text match after lowercasing + strip (highest confidence)
2. Substring match: any alias found as a substring of the input text
   - Longer aliases win over shorter ones (specificity)
3. No match → returns None (caller decides fallback)

All resolution is deterministic: same input always produces the same output.
No LLM, no fuzzy matching, no hidden inference.

Usage::

    resolver = SemanticResolver()

    # Resolve intent from raw user text
    result = resolver.resolve_intent("how much have i spent this week")
    # ResolveResult(canonical="usage", alias_matched="how much have i spent", confidence=1.0)

    # Resolve entity from raw text
    result = resolver.resolve_entity("my vault notes")
    # ResolveResult(canonical="vault", alias_matched="my vault", confidence=1.0)

    # Full preprocessing: normalize text for downstream slot fill
    normalized, meta = resolver.preprocess("token spend for gpt last 7 days")
    # normalized: "usage for model last 7 days"
    # meta: {"intent": ResolveResult(...), "entities": [...]}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .loader import SemanticMap, SemanticMapLoader


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class ResolveResult:
    """Result of a single alias resolution."""

    canonical: str  # Resolved canonical key
    alias_matched: str  # Which alias triggered the match
    confidence: float  # 1.0 = exact/substring; 0.0 = no match
    match_type: str  # "exact" | "substring" | "none"

    def __bool__(self) -> bool:
        return self.match_type != "none"


@dataclass
class PreprocessResult:
    """Result of preprocessing raw text through the semantic resolver."""

    normalized_text: str  # Text with aliases replaced
    intent_resolution: Optional[ResolveResult]  # Resolved intent (if any)
    entity_resolutions: List[ResolveResult] = field(default_factory=list)  # All entity hits
    resolution_metadata: Dict[str, object] = field(default_factory=dict)  # For debug/routing


# ---------------------------------------------------------------------------
# SemanticResolver
# ---------------------------------------------------------------------------
class SemanticResolver:
    """
    Deterministic alias → canonical resolver for intents and entities.

    Thread-safe (read-only after construction). Loads the semantic map
    lazily on first use.

    Args:
        loader: SemanticMapLoader instance. Defaults to bundled map.
    """

    def __init__(self, loader: Optional[SemanticMapLoader] = None) -> None:
        self._loader = loader or SemanticMapLoader()
        self._map: Optional[SemanticMap] = None

    @property
    def map(self) -> SemanticMap:
        """Lazily loaded semantic map."""
        if self._map is None:
            self._map = self._loader.load()
        return self._map

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def resolve_intent(self, text: str) -> Optional[ResolveResult]:
        """
        Resolve user text to a canonical intent.

        Args:
            text: Raw user input string.

        Returns:
            ResolveResult if matched, None if no alias matches.
        """
        return self._resolve(text, self.map.intent_alias_index)

    def resolve_entity(self, text: str) -> Optional[ResolveResult]:
        """
        Resolve user text to a canonical entity.

        Args:
            text: Raw user input string.

        Returns:
            ResolveResult if matched, None if no alias matches.
        """
        return self._resolve(text, self.map.entity_alias_index)

    def resolve_all_entities(self, text: str) -> List[ResolveResult]:
        """
        Find all entity aliases in text and return their resolved canonicals.

        Useful for slot fill preprocessing: identifies all mentioned entities.

        Args:
            text: Raw user input string.

        Returns:
            List of ResolveResults (may be empty).
        """
        lowered = text.lower()
        results: List[ResolveResult] = []
        seen_canonicals: set = set()

        # Sort aliases by length descending so longer (more specific) matches win
        sorted_aliases = sorted(
            self.map.entity_alias_index.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )

        for alias, canonical in sorted_aliases:
            if canonical in seen_canonicals:
                continue
            if self._alias_in_text(alias, lowered):
                results.append(
                    ResolveResult(
                        canonical=canonical,
                        alias_matched=alias,
                        confidence=1.0,
                        match_type="substring",
                    )
                )
                seen_canonicals.add(canonical)

        return results

    def preprocess(self, text: str) -> Tuple[str, PreprocessResult]:
        """
        Normalize raw text by resolving aliases to canonical terms.

        Replaces first matched intent alias and all entity aliases in
        the text with their canonical keys. Returns normalized text and
        metadata describing what was resolved.

        This output feeds directly into _classify_intent() and SlotFiller.

        Args:
            text: Raw user input.

        Returns:
            Tuple of (normalized_text, PreprocessResult).
        """
        lowered = text.lower()
        normalized = lowered
        metadata: Dict[str, object] = {}

        # 1. Resolve intent
        intent_result = self.resolve_intent(text)
        if intent_result:
            metadata["intent_alias"] = intent_result.alias_matched
            metadata["intent_canonical"] = intent_result.canonical

        # 2. Resolve all entities and replace in text
        entity_results = self.resolve_all_entities(text)
        for er in entity_results:
            # Replace alias with canonical in normalized text
            normalized = self._replace_alias(normalized, er.alias_matched, er.canonical)

        # 3. Replace intent alias with canonical (after entity replacements)
        if intent_result and intent_result.alias_matched in normalized:
            normalized = self._replace_alias(
                normalized, intent_result.alias_matched, intent_result.canonical
            )

        metadata["entity_aliases"] = [
            {"alias": er.alias_matched, "canonical": er.canonical} for er in entity_results
        ]
        metadata["normalized"] = normalized

        return normalized, PreprocessResult(
            normalized_text=normalized,
            intent_resolution=intent_result,
            entity_resolutions=entity_results,
            resolution_metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve(
        self,
        text: str,
        alias_index: Dict[str, str],
    ) -> Optional[ResolveResult]:
        """
        Core resolution: find best alias match for text.

        Priority: exact > longest substring.
        """
        lowered = text.strip().lower()

        # 1. Exact full-text match
        if lowered in alias_index:
            return ResolveResult(
                canonical=alias_index[lowered],
                alias_matched=lowered,
                confidence=1.0,
                match_type="exact",
            )

        # 2. Substring match — prefer longer aliases (more specific)
        best_alias: Optional[str] = None
        best_canonical: Optional[str] = None
        best_len = 0

        for alias, canonical in alias_index.items():
            if len(alias) <= best_len:
                continue
            if self._alias_in_text(alias, lowered):
                best_alias = alias
                best_canonical = canonical
                best_len = len(alias)

        if best_alias is not None and best_canonical is not None:
            return ResolveResult(
                canonical=best_canonical,
                alias_matched=best_alias,
                confidence=1.0,
                match_type="substring",
            )

        return None

    @staticmethod
    def _alias_in_text(alias: str, text: str) -> bool:
        """
        Check if alias appears in text.

        For single-word aliases: whole-word boundary match.
        For multi-word aliases: simple substring (phrase matching).
        """
        if " " in alias:
            return alias in text
        # Single-word: word boundary check
        return bool(re.search(rf"\b{re.escape(alias)}\b", text))

    @staticmethod
    def _replace_alias(text: str, alias: str, canonical: str) -> str:
        """Replace alias in text with canonical (case-insensitive, first occurrence)."""
        if " " in alias:
            return text.replace(alias, canonical, 1)
        return re.sub(rf"\b{re.escape(alias)}\b", canonical, text, count=1)


# ---------------------------------------------------------------------------
# Module-level singleton (shared, lazy-loaded)
# ---------------------------------------------------------------------------
_default_resolver: Optional[SemanticResolver] = None


def get_default_resolver() -> SemanticResolver:
    """Return the module-level default resolver (lazy-initialized)."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = SemanticResolver()
    return _default_resolver


def resolve_intent(text: str) -> Optional[ResolveResult]:
    """Convenience: resolve intent using the default resolver."""
    return get_default_resolver().resolve_intent(text)


def preprocess(text: str) -> Tuple[str, PreprocessResult]:
    """Convenience: preprocess text using the default resolver."""
    return get_default_resolver().preprocess(text)
