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


@dataclass
class CanonicalEmbeddingRequest:
    """Provider-neutral embedding request structure used by embedding adapters."""

    model: str
    input: List[str] = field(default_factory=list)
    dimensions: Optional[int] = None
    encoding_format: str = "float"
    input_type: Optional[str] = None
    task: Optional[str] = None
    truncate: bool = True
    normalized: bool = False
    raw_extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalEmbeddingResponse:
    """Provider-neutral embedding response structure produced by embedding adapters."""

    data: List[Dict[str, Any]] = field(default_factory=list)
    model: str = "unknown"
    usage: Dict[str, Any] = field(default_factory=dict)
    tokenpak_meta: Dict[str, Any] = field(default_factory=dict)
