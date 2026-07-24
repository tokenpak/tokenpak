"""Adapter abstraction utilities — module-level wrappers around the adapter registry.

Transferred from monolith (packages/core/tokenpak/runtime/proxy.py lines 3348–3399)
as part of TPK-CONSOLIDATION-A2c (Section 2.6 Adapter abstraction).

Provides module-level functions that delegate to the per-format adapter instances:

- _header_mapping()        — build plain dict from BaseHTTPRequestHandler headers
- _detect_adapter()        — detect adapter for path/headers/body via registry
- extract_request_tokens() — extract model name + token count from request body
- extract_response_tokens() — extract output token count from response body
- extract_query_signal()   — extract search query signal for vault context
"""

from __future__ import annotations

__all__ = (
    "FormatAdapter",
    "extract_query_signal",
    "extract_request_tokens",
    "extract_response_tokens",
)


import threading
from typing import Any, Dict, Mapping, Optional, Tuple

from .base import FormatAdapter
from .registry import AdapterRegistry

# ---------------------------------------------------------------------------
# Registry singleton — lazy initialised to avoid import-time side effects
# ---------------------------------------------------------------------------
_REGISTRY: AdapterRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def _get_registry() -> AdapterRegistry:
    """Return the default adapter registry singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                from . import build_default_registry

                _REGISTRY = build_default_registry()
    return _REGISTRY


# ---------------------------------------------------------------------------
# Public API — mirrors monolith module-level functions
# ---------------------------------------------------------------------------


def _header_mapping(headers: Any) -> Dict[str, str]:
    """Build a plain dict from BaseHTTPRequestHandler headers."""
    result: Dict[str, str] = {}
    try:
        for key in headers:
            result[str(key)] = str(headers[key])
    except Exception:
        pass
    return result


def _detect_adapter(
    path: str, headers: Mapping[str, str], body_bytes: Optional[bytes] = None
) -> FormatAdapter:
    """Detect the format adapter for the given request via the default registry."""
    return _get_registry().detect(path=path, headers=headers, body=body_bytes)


def extract_request_tokens(
    body_bytes: bytes, adapter: Optional[FormatAdapter] = None
) -> Tuple[str, int]:
    """Extract model name and input token count from a request body.

    Returns:
        ``(model_name, token_count)`` — "unknown"/0 on any error.
    """
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_request_tokens(body_bytes, token_counter=None)
    except Exception:
        return "unknown", 0


def extract_response_tokens(
    body_bytes: bytes,
    adapter: Optional[FormatAdapter] = None,
    is_sse: bool = False,
) -> int:
    """Extract output token count from a response body.

    Returns:
        Token count — 0 on any error.
    """
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_response_tokens(body_bytes, is_sse=is_sse)
    except Exception:
        return 0


def extract_query_signal(body_bytes: bytes, adapter: Optional[FormatAdapter] = None) -> str:
    """Extract a search query signal from the request body for vault context.

    Returns:
        Query string — empty string on any error.
    """
    try:
        active_adapter = adapter or _detect_adapter("", {}, body_bytes)
        return active_adapter.extract_query_signal(body_bytes)
    except Exception:
        return ""
