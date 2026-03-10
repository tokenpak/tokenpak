"""Canonical request/response types for provider-agnostic proxy processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


SystemType = Union[str, List[Dict[str, Any]], None]


@dataclass
class CanonicalRequest:
    """Provider-neutral request structure used by proxy processing stages."""

    model: str
    system: SystemType = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tools: Optional[List[Dict[str, Any]]] = None
    generation: Dict[str, Any] = field(default_factory=dict)
    stream: bool = False
    raw_extra: Dict[str, Any] = field(default_factory=dict)
    source_format: str = "unknown"


@dataclass
class CanonicalResponse:
    """Provider-neutral response structure used for token usage extraction."""

    model: str = "unknown"
    usage: Dict[str, Any] = field(default_factory=dict)
    content: Any = None
    raw_extra: Dict[str, Any] = field(default_factory=dict)
    source_format: str = "unknown"
