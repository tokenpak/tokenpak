"""TIP capability labels <-> MCP capability-negotiation frame translation.

TIP-1.0 capability labels (``core.contracts.capabilities``) are the
authoritative set a TokenPak component can publish. MCP's capability-
negotiation frames carry those labels between peers. This module is
the one-way translator - never duplicates the label set or invents
labels not in the registry.

The authoritative label catalog lives in
``schemas/tip/capabilities.schema.json`` in the registry repo; Phase 3
populates it. Until then, this module's ``validate`` path is permissive
(any ``tip.*`` or ``ext.*`` label is accepted).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALID_LABEL = re.compile(r"^(tip|ext)\.[a-z0-9._-]+$")


@dataclass(slots=True)
class CapabilityFrame:
    """An MCP capability-negotiation frame as we exchange it on the wire."""

    labels: frozenset[str]


def to_frame(labels: frozenset[str]) -> CapabilityFrame:
    """Build an outgoing MCP capability frame from a TIP label set."""
    for label in labels:
        if not _VALID_LABEL.match(label):
            raise ValueError(
                f"invalid TIP capability label {label!r}; "
                "expected tip.<name> or ext.<ns>.<name>"
            )
    return CapabilityFrame(labels=labels)


def from_frame(frame: CapabilityFrame) -> frozenset[str]:
    """Accept an incoming MCP capability frame; return its TIP label set.

    Silently ignores labels that don't match the ``tip.*``/``ext.*``
    pattern (forward-compat: unknown namespaces are not a protocol
    error, they're just not usable as TIP capabilities).
    """
    return frozenset(l for l in frame.labels if _VALID_LABEL.match(l))
