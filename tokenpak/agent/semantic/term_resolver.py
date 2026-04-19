"""
Deterministic Term-Card Resolver for TokenPak Runtime.

Provides cache-stable term resolution by:
- Loading term_cards.json glossary
- Extracting matched terms from text (canonical + aliases)
- Returning canonical IDs with short card snippets
- Enforcing strict runtime policy (top-K, short fields, zero injection by default)
- Handling ambiguity deterministically (one question or fallback)

This enables safe integration into proxy_v4 request handling without prompt
stuffing or full glossary dumps.

Usage::

    from tokenpak.agent.semantic import TermResolver, TermResolverConfig

    config = TermResolverConfig(top_k=3, max_bytes_per_card=200)
    resolver = TermResolver(config=config)
    
    result = resolver.resolve_terms("baseline_cost and actual_cost comparison")
    print(result.canonical_ids)  # ["baseline_cost", "actual_cost"]
    print(result.ambiguous)  # False
    print(result.injection_text)  # Formatted snippet
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import threading


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class TermCardSnippet:
    """Short-form glossary snippet for injection."""
    canonical_id: str
    meaning: str  # from 'what' field
    aliases: List[str]  # top aliases only
    confidence: float
    
    def to_injection_format(self) -> str:
        """Format for inclusion in prompt context."""
        aliases_str = ", ".join(self.aliases[:2]) if self.aliases else ""
        alias_part = f" (also: {aliases_str})" if aliases_str else ""
        return f"**{self.canonical_id}**{alias_part}: {self.meaning}"


@dataclass
class TermResolution:
    """Result of term resolution."""
    query: str
    canonical_ids: List[str]  # matched canonical term IDs
    card_snippets: List[TermCardSnippet]  # top-K short-form cards
    ambiguous: bool = False
    ambiguity_question: Optional[str] = None  # if ambiguous
    injection_text: Optional[str] = None  # formatted for prompt injection
    tokens_estimate: int = 0  # rough token count
    

@dataclass
class TermResolverConfig:
    """Configuration for term resolution behavior."""
    top_k: int = 3  # max cards to return (hard cap 5)
    max_bytes_per_card: int = 200  # max bytes per card snippet
    enabled: bool = True
    
    def __post_init__(self):
        self.top_k = min(self.top_k, 5)  # enforce hard cap
        self.top_k = max(self.top_k, 1)  # enforce minimum


class TermResolver:
    """Deterministic resolver for glossary terms."""
    
    def __init__(
        self,
        cards_path: Optional[Path] = None,
        config: Optional[TermResolverConfig] = None,
    ):
        """
        Initialize resolver.
        
        Args:
            cards_path: Path to term_cards.json. Auto-discovers if not provided.
            config: TermResolverConfig with resolution parameters.
        """
        self.config = config or TermResolverConfig()
        self._cards: Dict[str, Dict[str, Any]] = {}
        self._aliases_index: Dict[str, str] = {}  # alias -> canonical_id
        self._lock = threading.Lock()
        
        # Auto-discover if not provided
        if cards_path is None:
            cards_path = self._find_term_cards()
        
        if cards_path and cards_path.exists():
            self.load_cards(cards_path)
    
    @staticmethod
    def _find_term_cards() -> Optional[Path]:
        """Locate term_cards.json in the tokenpak module directory."""
        # Look for term_cards.json in tokenpak root
        module_root = Path(__file__).parent.parent.parent
        candidates = [
            module_root / "term_cards.json",
            Path.home() / "Projects" / "tokenpak" / "tokenpak" / "term_cards.json",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None
    
    def load_cards(self, cards_path: Path) -> None:
        """Load and parse term_cards.json."""
        try:
            data = json.loads(cards_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            print(f"⚠️ Failed to load term cards from {cards_path}: {e}")
            return
        
        with self._lock:
            self._cards = data
            self._aliases_index = {}
            
            # Build aliases index: alias -> canonical_id
            for term_id, card in data.items():
                if isinstance(card, dict) and "aliases" in card:
                    for alias in card.get("aliases", []):
                        self._aliases_index[self._normalize_term(alias)] = term_id
                # Also index the canonical term itself
                self._aliases_index[self._normalize_term(term_id)] = term_id
    
    @staticmethod
    def _normalize_term(term: str) -> str:
        """Normalize term for matching: lowercase, underscores to spaces."""
        return term.lower().replace("_", " ").replace("-", " ").strip()
    
    def resolve_terms(self, text: str) -> TermResolution:
        """
        Extract and resolve glossary terms from text.
        
        Args:
            text: User input text to analyze.
            
        Returns:
            TermResolution with canonical IDs, snippets, and ambiguity info.
        """
        if not self.config.enabled or not self._cards:
            # No-op: return empty resolution
            return TermResolution(
                query=text,
                canonical_ids=[],
                card_snippets=[],
                ambiguous=False,
                injection_text=None,
                tokens_estimate=0,
            )
        
        with self._lock:
            cards = dict(self._cards)
            aliases_index = dict(self._aliases_index)
        
        # Extract matched terms (avoid duplicates via set)
        matched_ids: Dict[str, int] = {}  # canonical_id -> match_count
        
        # Strategy: find longest matching phrases first (greedy left-to-right)
        # to avoid subword matches
        normalized_text = self._normalize_term(text)
        text_lower = text.lower()
        
        # Try to match terms in order of confidence/tier (higher first)
        sorted_terms = sorted(
            cards.items(),
            key=lambda x: (
                -x[1].get("tier", 0),  # higher tier first
                -x[1].get("confidence", 0.5),  # higher confidence first
            ),
        )
        
        for term_id, card in sorted_terms:
            # Check canonical term
            canonical_normalized = self._normalize_term(term_id)
            if self._match_term_in_text(canonical_normalized, text_lower):
                matched_ids[term_id] = matched_ids.get(term_id, 0) + 1
            
            # Check aliases
            for alias in card.get("aliases", []):
                alias_normalized = self._normalize_term(alias)
                if self._match_term_in_text(alias_normalized, text_lower):
                    matched_ids[term_id] = matched_ids.get(term_id, 0) + 1
                    break  # count match once per term
        
        canonical_ids = list(matched_ids.keys())[:self.config.top_k]
        
        # Check for ambiguity
        ambiguous, ambiguity_q = self._check_ambiguity(canonical_ids, cards)
        
        # Build snippets
        snippets = self._build_snippets(canonical_ids, cards)
        
        # Format injection text
        injection_text = self._format_injection(snippets)
        tokens_est = self._estimate_tokens(injection_text)
        
        return TermResolution(
            query=text,
            canonical_ids=canonical_ids,
            card_snippets=snippets,
            ambiguous=ambiguous,
            ambiguity_question=ambiguity_q,
            injection_text=injection_text,
            tokens_estimate=tokens_est,
        )
    
    @staticmethod
    def _match_term_in_text(term: str, text_lower: str) -> bool:
        """Check if term appears in text as a whole word or phrase."""
        # Normalize text: replace underscores/hyphens with spaces for matching
        normalized_text = text_lower.replace("_", " ").replace("-", " ")
        
        # Use word boundaries for single words, spaces for phrases
        if " " in term:
            # Phrase: must appear surrounded by whitespace/punctuation
            return f" {term} " in f" {normalized_text} " or f" {term}." in f" {normalized_text}." or f" {term}," in f" {normalized_text},"
        else:
            # Single word: use word boundary regex
            pattern = rf"\b{re.escape(term)}\b"
            return bool(re.search(pattern, normalized_text))
    
    def _check_ambiguity(
        self, canonical_ids: List[str], cards: Dict[str, Dict[str, Any]]
    ) -> Tuple[bool, Optional[str]]:
        """
        Deterministically check if resolution is ambiguous.
        
        Returns (is_ambiguous, question_or_none).
        """
        if len(canonical_ids) <= 1:
            return False, None
        
        # Multiple matches = ambiguous
        # Generate deterministic question based on first two terms
        if len(canonical_ids) >= 2:
            term_a = canonical_ids[0]
            term_b = canonical_ids[1]
            card_a = cards.get(term_a, {})
            card_b = cards.get(term_b, {})
            
            q = f"Did you mean '{term_a}' ({card_a.get('what', '?')[:40]}...) or '{term_b}' ({card_b.get('what', '?')[:40]}...)?'"
            return True, q
        
        return False, None
    
    def _build_snippets(
        self, canonical_ids: List[str], cards: Dict[str, Dict[str, Any]]
    ) -> List[TermCardSnippet]:
        """Build short-form card snippets for injection."""
        snippets = []
        
        for cid in canonical_ids[:self.config.top_k]:
            card = cards.get(cid, {})
            if not card:
                continue
            
            meaning = card.get("what", "")[:self.config.max_bytes_per_card]
            aliases = card.get("aliases", [])[:2]  # top 2 aliases only
            confidence = card.get("confidence", 0.5)
            
            snippet = TermCardSnippet(
                canonical_id=cid,
                meaning=meaning,
                aliases=aliases,
                confidence=confidence,
            )
            snippets.append(snippet)
        
        return snippets
    
    def _format_injection(self, snippets: List[TermCardSnippet]) -> Optional[str]:
        """Format snippets for prompt injection."""
        if not snippets:
            return None
        
        # Deterministic header
        lines = ["\n## Glossary Terms\n"]
        for snippet in snippets:
            lines.append(snippet.to_injection_format())
        
        return "\n".join(lines)
    
    @staticmethod
    def _estimate_tokens(text: Optional[str]) -> int:
        """Rough token estimate: 1 token ≈ 4 chars."""
        if not text:
            return 0
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

_global_resolver: Optional[TermResolver] = None
_resolver_lock = threading.Lock()


def resolve_terms(
    text: str,
    config: Optional[TermResolverConfig] = None,
) -> TermResolution:
    """
    Resolve terms in text using the global resolver instance.
    
    This is the primary entry point for term resolution in proxy_v4.
    
    Args:
        text: User input text to analyze.
        config: Optional override config (uses global if not provided).
        
    Returns:
        TermResolution with matched terms and snippets.
    """
    global _global_resolver
    
    if config is not None:
        # Use provided config
        resolver = TermResolver(config=config)
        return resolver.resolve_terms(text)
    
    # Use global instance
    with _resolver_lock:
        if _global_resolver is None:
            _global_resolver = TermResolver()
    
    return _global_resolver.resolve_terms(text)
