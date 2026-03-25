"""tokenpak.agent.semantic — Semantic KB layer: term-card generation, resolution and glossary integration."""

from .term_card_builder import (
    build,
    detect_alias_conflicts,
    enforce_caps,
    lazy_add,
    load_cards,
    save_cards,
    sort_cards,
    validate_card,
    validation_report,
)
from .term_resolver import (
    TermCardSnippet,
    TermResolution,
    TermResolver,
    TermResolverConfig,
    resolve_terms,
)

__all__ = [
    "build",
    "detect_alias_conflicts",
    "enforce_caps",
    "lazy_add",
    "load_cards",
    "save_cards",
    "sort_cards",
    "validate_card",
    "validation_report",
    "TermResolver",
    "TermResolverConfig",
    "resolve_terms",
    "TermCardSnippet",
    "TermResolution",
]
