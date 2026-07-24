"""Term Card Resolver — Stub implementation.

Placeholder for future term card resolution system.
This module is referenced in tasks but not yet fully implemented.

DEFERRED: Implement actual term card resolution logic when needed.
"""

from __future__ import annotations

from collections.abc import Mapping


class TermCardResolver:
    """Placeholder for term card resolution."""

    def __init__(self) -> None:
        self.enabled = False
        self.available = False

    def resolve(self, term: str, context: Mapping[str, object] | None = None) -> dict[str, object]:
        """Placeholder resolver — returns empty."""
        return {}


__all__ = ["TermCardResolver"]
