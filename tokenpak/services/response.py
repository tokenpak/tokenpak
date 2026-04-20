"""Wire-format-agnostic Response + Chunk types crossing the services boundary.

Returned by ``services.execute(...)`` and yielded by ``services.stream(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Response:
    """Canonical response type returned by services.execute().

    Phase 2 scaffold - minimal shape. Fields land as pipeline logic lands.
    """

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    """Single streaming frame yielded by services.stream()."""

    body: bytes = b""
    terminal: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
