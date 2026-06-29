"""Dispatch capability registry.

The capability enum is the single source of truth for the strings that may
appear in ``DispatchWorker.capabilities`` and ``DispatchRoute.stations[].
required_capabilities``. Per the governance rule, the worker registry
loader MUST reject unknown capability strings **at load time** (fail-loud, not
skip-silently); :func:`validate_capabilities` implements that contract.

Adding a capability is a governed change (maintainer review required); do not
extend ``DISPATCH_CAPABILITIES`` without that change landing first.

Note: ``registry`` is a PEP 420 namespace package (no ``__init__.py``) so this
module stays strictly within the P-SCHEMA-01 ``expected_files_changed`` scope,
which enumerates ``registry/capabilities.py`` and not a package initializer.
"""

from __future__ import annotations

from collections.abc import Iterable

# v0.1-alpha capability enum (11 entries).
DISPATCH_CAPABILITIES: frozenset[str] = frozenset(
    {
        "answer_generation",
        "code_drafting",
        "code_editing",
        "patch_generation",
        "doc_drafting",
        "doc_review",
        "semantic_review",
        "test_planning",
        "test_execution",
        "repo_inspection",
        "artifact_packaging",
    }
)


class UnknownCapabilityError(ValueError):
    """Raised when a capability string is not in :data:`DISPATCH_CAPABILITIES`.

    Subclasses :class:`ValueError` so Pydantic field validators surface it as a
    standard validation error while still being catchable by exact type.
    """

    def __init__(self, unknown: Iterable[str]) -> None:
        self.unknown = sorted(set(unknown))
        known = ", ".join(sorted(DISPATCH_CAPABILITIES))
        super().__init__(
            "unknown Dispatch capability string(s): "
            f"{self.unknown!r}. Known capabilities: {known}."
        )


def is_known_capability(capability: str) -> bool:
    """Return ``True`` iff ``capability`` is a registered Dispatch capability."""

    return capability in DISPATCH_CAPABILITIES


def validate_capabilities(capabilities: Iterable[str]) -> list[str]:
    """Validate ``capabilities`` against the registry, fail-loud on unknowns.

    Returns the capabilities as a list (order preserved) when every entry is
    known. Raises :class:`UnknownCapabilityError` listing *all* offending
    strings when any entry is not in :data:`DISPATCH_CAPABILITIES`.

    This is the load-time rejection the capability registry contract mandates.
    """

    caps = list(capabilities)
    unknown = [c for c in caps if c not in DISPATCH_CAPABILITIES]
    if unknown:
        raise UnknownCapabilityError(unknown)
    return caps


__all__ = [
    "DISPATCH_CAPABILITIES",
    "UnknownCapabilityError",
    "is_known_capability",
    "validate_capabilities",
]
