"""Backend contract — what every dispatch backend implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from tokenpak.services.request import Request


@dataclass(slots=True)
class BackendResponse:
    """Backend-level response — status + headers + body bytes.

    Not the full :class:`tokenpak.services.response.Response` (which is
    wire-format-shaped). This is the raw transport outcome a Backend
    hands back to the pipeline so the caller can translate to whatever
    response shape it needs.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@runtime_checkable
class Backend(Protocol):
    """Every dispatch backend conforms to this protocol."""

    name: str

    def dispatch(self, request: Request) -> BackendResponse: ...


__all__ = ["Backend", "BackendResponse"]
