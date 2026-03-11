"""
Semantic layer for TokenPak — term resolution and glossary integration.
"""

from .term_resolver import (
    TermResolver,
    TermResolverConfig,
    resolve_terms,
    TermCardSnippet,
    TermResolution,
)

__all__ = [
    "TermResolver",
    "TermResolverConfig",
    "resolve_terms",
    "TermCardSnippet",
    "TermResolution",
]
