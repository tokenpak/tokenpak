"""
Directives — Pro-tier directive application (placeholder / stub).

In the OSS build DirectiveApplier is a no-op pass-through.
Pro integration will replace this with rule-based content transformations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class DirectiveApplier:
    """
    Apply compression directives to a messages list.

    OSS stub — passes messages through unmodified.
    Pro integration: replaces with rule-based directive engine.

    Parameters
    ----------
    directives : list[dict], optional
        List of directive dicts (Pro feature; ignored in OSS).
    """

    def __init__(self, directives: Optional[List[Dict[str, Any]]] = None) -> None:
        self._directives: List[Dict[str, Any]] = directives or []

    def apply(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Apply registered directives to messages.

        Parameters
        ----------
        messages : list[dict]
            Messages to process.

        Returns
        -------
        list[dict]
            Transformed messages (pass-through in OSS build).
        """
        # OSS: no directives applied
        return messages

    def add_directive(self, directive: Dict[str, Any]) -> None:
        """Register a directive (Pro feature placeholder)."""
        self._directives.append(directive)

    def clear(self) -> None:
        """Remove all registered directives."""
        self._directives.clear()

    @property
    def directive_count(self) -> int:
        return len(self._directives)
