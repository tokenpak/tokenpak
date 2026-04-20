"""
ToolSchemaRegistry — Frozen Tool Schema Cache for Prompt-Cache Stability

Tool schemas sent with each LLM request are large (10-20 KB) and stable.
If schemas are serialized differently each request (different key ordering,
whitespace, etc.) they produce different bytes, preventing Anthropic prompt
caching from matching.

This module solves the problem by:
  1. Normalizing the tools array deterministically (sorted by name, stable JSON)
  2. Freezing the result at first use and returning identical bytes every time
  3. Detecting actual schema changes (new/removed tools, changed descriptions)
     and updating the frozen copy only then

Usage in proxy::

    from tokenpak.agent.proxy.tool_schema_registry import get_registry

    # Normalize tools in the request body (returns new body bytes + metadata)
    new_body, changed = get_registry().normalize_request(body_bytes)

The registry is a module-level singleton, initialized on first call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively sort dict keys for deterministic JSON output."""
    if isinstance(schema, dict):
        return {k: _normalize_schema(v) for k, v in sorted(schema.items())}
    if isinstance(schema, list):
        return [_normalize_schema(item) for item in schema]
    return schema


def _normalize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return a deterministically ordered, deterministically serialized copy of tools.

    Sorting strategy:
      - Primary: tool name (alphabetical)
      - All nested dict keys sorted recursively
    """
    normalized = []
    for tool in tools:
        norm = _normalize_schema(tool)
        normalized.append(norm)
    # Sort by tool name (primary identifier)
    normalized.sort(key=lambda t: t.get("name", ""))
    return normalized


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _serialize(tools: list[dict[str, Any]]) -> str:
    """Produce stable, compact JSON for the tools array."""
    return json.dumps(tools, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _count_tokens_approx(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# ToolSchemaRegistry
# ---------------------------------------------------------------------------


class ToolSchemaRegistry:
    """
    Singleton registry that freezes tool schemas for prompt-cache stability.

    Thread-safe. The frozen text is updated only when tools actually change.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Frozen state
        self._frozen_tools: list[dict[str, Any]] | None = None
        self._frozen_text: str | None = None
        self._frozen_hash: str | None = None
        self._frozen_at: float = 0.0

        # Stats
        self.total_requests: int = 0
        self.schema_changes: int = 0
        self.bytes_saved: int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def normalize_request(self, body_bytes: bytes) -> tuple[bytes, bool]:
        """
        Parse the request body, normalize its ``tools`` array (if present),
        and return (new_body_bytes, changed) where ``changed`` is True if the
        tool schemas differ from the previously frozen version.

        If no ``tools`` key is present, returns the original body unchanged.
        """
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body_bytes, False

        tools = data.get("tools")
        if not tools or not isinstance(tools, list):
            return body_bytes, False

        normalized = _normalize_tools(tools)
        new_text = _serialize(normalized)
        new_hash = _sha256(new_text)

        with self._lock:
            self.total_requests += 1
            changed = False

            if self._frozen_hash is None:
                # First time — freeze
                self._freeze(normalized, new_text, new_hash)
                logger.info(
                    "[tool_schema_registry] Frozen %d tools (%d chars, ~%d tokens, hash=%s)",
                    len(normalized),
                    len(new_text),
                    _count_tokens_approx(new_text),
                    new_hash[:12],
                )
            elif new_hash != self._frozen_hash:
                # Schemas actually changed — update frozen copy
                old_hash = self._frozen_hash
                self._freeze(normalized, new_text, new_hash)
                self.schema_changes += 1
                changed = True
                logger.warning(
                    "[tool_schema_registry] Tool schemas changed (old=%s new=%s). "
                    "Cache will be invalidated for this request.",
                    old_hash[:12],
                    new_hash[:12],
                )

            # Replace tools with frozen copy (byte-for-byte identical every request)
            len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))
            data["tools"] = self._frozen_tools
            new_body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

            # Track bytes saved vs naive round-trip (minor; main gain is cache hits)
            self.bytes_saved += max(0, len(body_bytes) - len(new_body))

        return new_body, changed

    def get_frozen_text(self) -> str | None:
        """Return the current frozen tools JSON text (for diagnostics)."""
        with self._lock:
            return self._frozen_text

    def get_frozen_hash(self) -> str | None:
        """Return SHA-256 of frozen tools (first 16 hex chars)."""
        with self._lock:
            return self._frozen_hash[:16] if self._frozen_hash else None

    def stats(self) -> dict:
        with self._lock:
            frozen_tools = self._frozen_tools or []
            frozen_text = self._frozen_text or ""
            return {
                "frozen_tools": len(frozen_tools),
                "frozen_bytes": len(frozen_text.encode("utf-8")),
                "frozen_tokens_approx": _count_tokens_approx(frozen_text),
                "frozen_hash": self._frozen_hash[:16] if self._frozen_hash else None,
                "frozen_at": self._frozen_at,
                "total_requests": self.total_requests,
                "schema_changes": self.schema_changes,
                "bytes_saved": self.bytes_saved,
            }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _freeze(
        self,
        normalized: list[dict[str, Any]],
        text: str,
        hash_: str,
    ) -> None:
        """Store the frozen state. Caller must hold self._lock."""
        self._frozen_tools = normalized
        self._frozen_text = text
        self._frozen_hash = hash_
        self._frozen_at = time.time()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: ToolSchemaRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ToolSchemaRegistry:
    """Return the module-level ToolSchemaRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ToolSchemaRegistry()
    return _registry


# ---------------------------------------------------------------------------
# FROZEN_TOOL_SCHEMAS — module-level accessor for acceptance criteria
# ---------------------------------------------------------------------------
# Returns the frozen schemas from the singleton registry.
# The actual frozen data lives inside get_registry()._frozen_tools and is
# updated only when tool schemas genuinely change.


def _get_frozen_tool_schemas() -> list:
    """Return currently frozen tool schemas (or empty list if not yet initialized)."""
    reg = get_registry()
    with reg._lock:
        return reg._frozen_tools or []


# Module-level constant alias: call FROZEN_TOOL_SCHEMAS() to get the current frozen list.
# Use get_registry().normalize_request(body) in the request pipeline (preferred).
FROZEN_TOOL_SCHEMAS = _get_frozen_tool_schemas

__all__ = ["ToolSchemaRegistry", "get_registry", "FROZEN_TOOL_SCHEMAS", "_get_frozen_tool_schemas"]
