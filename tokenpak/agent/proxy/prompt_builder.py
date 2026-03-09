"""
PromptBuilder — Stable/Volatile Prefix Split for Anthropic Prompt Caching

Splits the prompt into two logical tiers:

  STABLE PREFIX  — system prompt blocks, frozen tool schemas, static policies
                   → gets cache_control: {type: ephemeral}
                   → should be identical (byte-for-byte) across consecutive requests

  VOLATILE TAIL  — vault injection, user message, dynamic retrieved context
                   → NO cache_control
                   → changes every request, should stay after the cache boundary

Why this matters
----------------
Anthropic prompt caching caches the prefix UP TO the last cache_control block.
If dynamic content (vault search results, per-request context) is placed before
the cache_control marker, the cache key changes every request → 0% cache hits.

The fix: place cache_control at the END of the stable system blocks, and append
all volatile content AFTER it.  Vault injection already does this, but only when
it actually injects content.  PromptBuilder ensures cache_control is applied
ALWAYS — even for short requests that skip injection.

Usage in proxy::

    from tokenpak.agent.proxy.prompt_builder import apply_stable_cache_control

    # Apply to every Anthropic request (before forwarding):
    body_bytes = apply_stable_cache_control(body_bytes)

    # Or, when doing vault injection:
    body_bytes = inject_with_cache_boundary(body_bytes, volatile_text)

Architecture diagram
--------------------

    ┌─────────────────────────────────────────────────────────────────┐
    │  REQUEST BODY (Anthropic messages API)                          │
    │                                                                 │
    │  "system": [                                                    │
    │    { type: text, text: "<SOUL.md + project context...>" },      │
    │    { type: text, text: "<injected files, memory...>" },         │
    │    { type: text, text: "<last stable block>",                   │
    │             cache_control: { type: "ephemeral" }  }  ← MARKER  │
    │    { type: text, text: "<vault BM25 injection...>" },  ← VOLATILE│
    │  ]                                                              │
    │                                                                 │
    │  "tools": [ <deterministically sorted, frozen by registry> ]   │
    │                                                                 │
    │  "messages": [                                                  │
    │    { role: user, content: "<user message>" }  ← VOLATILE       │
    │  ]                                                              │
    └─────────────────────────────────────────────────────────────────┘

Cache hit expectation
---------------------
  Before: cache_control only applied when vault injection is active
          → short requests / haiku-skip → 0 cache markers → low hit rate
  After:  cache_control applied to ALL requests with a system prompt
          → consistent cache prefix across all request types
          → target: 85-92% cache hit rate across all models/sizes

Components
----------
  - apply_stable_cache_control(body_bytes) → mark static system prefix (no injection)
  - inject_with_cache_boundary(body_bytes, volatile_text) → inject + mark
  - classify_system_blocks(blocks) → stable vs volatile heuristic
  - PromptCacheStats — tracks per-session cache placement stats
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Heuristics for detecting dynamic content in system blocks
# ---------------------------------------------------------------------------

# Patterns that indicate a block contains dynamic/volatile content
_VOLATILE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),  # ISO timestamps
    re.compile(r"\btoday is\b", re.IGNORECASE),
    re.compile(r"\bcurrent time\b", re.IGNORECASE),
    re.compile(r"\bcurrent date\b", re.IGNORECASE),
    re.compile(r"<retrieved_context>", re.IGNORECASE),
    re.compile(r"<vault_context>", re.IGNORECASE),
    re.compile(r"\[vault injection\]", re.IGNORECASE),
    re.compile(r"--- \[.*?\] \(relevance:", re.IGNORECASE),  # vault block headers
]


def _is_volatile_block(text: str) -> bool:
    """Return True if a system block looks dynamic/volatile."""
    for pat in _VOLATILE_PATTERNS:
        if pat.search(text):
            return True
    return False


def classify_system_blocks(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split system blocks into (stable_blocks, volatile_blocks).

    Stable blocks: no volatile patterns detected.
    Volatile blocks: contain dynamic content (timestamps, retrieved context, etc.)

    The last stable block is the intended cache_control anchor.
    """
    stable: list[dict[str, Any]] = []
    volatile: list[dict[str, Any]] = []

    for block in blocks:
        if not isinstance(block, dict):
            stable.append(block)
            continue
        text = block.get("text", "") if block.get("type") == "text" else ""
        if _is_volatile_block(text):
            volatile.append(block)
        else:
            stable.append(block)

    return stable, volatile


def _mark_last_block_cacheable(
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Return a copy of blocks with cache_control: ephemeral on the last element.
    Idempotent — won't double-mark.
    """
    if not blocks:
        return blocks

    result = [dict(b) for b in blocks]  # shallow copy

    # Find last text block to mark
    for i in range(len(result) - 1, -1, -1):
        blk = result[i]
        if isinstance(blk, dict) and blk.get("type") == "text":
            if blk.get("cache_control") != {"type": "ephemeral"}:
                result[i] = dict(blk, cache_control={"type": "ephemeral"})
            return result

    return result


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def apply_stable_cache_control(body_bytes: bytes) -> bytes:
    """
    Ensure the stable system prefix has cache_control: ephemeral.

    Intended for ALL Anthropic requests — not just those with vault injection.
    Idempotent: if cache_control is already placed, does nothing.

    Returns the (possibly modified) body bytes.
    """
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body_bytes

    system = data.get("system")
    if not system:
        return body_bytes

    # Normalize string system prompt → list form
    if isinstance(system, str):
        if not system.strip():
            return body_bytes
        data["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    if not isinstance(system, list):
        return body_bytes

    # Already has cache_control somewhere? Check if it's on the last stable block.
    # If any block already has cache_control, respect existing placement.
    has_cache_control = any(isinstance(b, dict) and b.get("cache_control") for b in system)
    if has_cache_control:
        return body_bytes  # already handled

    # Classify blocks: stable vs volatile
    stable_blocks, volatile_blocks = classify_system_blocks(system)

    if not stable_blocks:
        return body_bytes  # nothing to mark

    # Mark last stable block
    stable_blocks = _mark_last_block_cacheable(stable_blocks)

    # Reassemble: stable (with marker) + volatile
    data["system"] = stable_blocks + volatile_blocks

    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def inject_with_cache_boundary(
    body_bytes: bytes,
    volatile_text: str,
) -> bytes:
    """
    Inject volatile_text into the system prompt AFTER the cache boundary.

    1. Ensures the last existing system block has cache_control: ephemeral
    2. Appends volatile_text as a new block WITHOUT cache_control

    This is the correct pattern for vault injection.
    Returns updated body bytes.
    """
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body_bytes

    system = data.get("system", "")
    volatile_block = {"type": "text", "text": volatile_text}
    # No cache_control on volatile block — it's dynamic

    if isinstance(system, str):
        data["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            volatile_block,
        ]
    elif isinstance(system, list):
        if not system:
            data["system"] = [volatile_block]
        else:
            # Mark last existing block as cache boundary (idempotent)
            marked = _mark_last_block_cacheable(system)
            data["system"] = marked + [volatile_block]
    else:
        data["system"] = volatile_block["text"]

    return json.dumps(data, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# PromptBuilder — higher-level builder for structured prompt assembly
# ---------------------------------------------------------------------------


@dataclass
class PromptParts:
    """Decomposed prompt parts for inspection and reassembly."""

    stable_blocks: list[dict[str, Any]] = field(default_factory=list)
    volatile_blocks: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    other_fields: dict[str, Any] = field(default_factory=dict)

    def to_request_body(self) -> dict[str, Any]:
        """Reassemble into a complete Anthropic request body."""
        body: dict[str, Any] = dict(self.other_fields)
        body["system"] = self.stable_blocks + self.volatile_blocks
        if self.tools:
            body["tools"] = self.tools
        body["messages"] = self.messages
        return body


class PromptBuilder:
    """
    Stateless prompt builder that separates stable from volatile content.

    Typical use in proxy::

        builder = PromptBuilder()
        parts = builder.decompose(body_bytes)

        # Add vault injection to volatile tail
        if vault_text:
            parts.volatile_blocks.append({"type": "text", "text": vault_text})

        # Get final body with cache_control correctly placed
        new_body = builder.build(parts)

    The builder:
      - Classifies existing system blocks as stable vs volatile
      - Marks last stable block with cache_control: ephemeral
      - Does NOT cache_control volatile blocks
      - Preserves tool schemas (frozen externally by tool_schema_registry)
    """

    def decompose(self, body_bytes: bytes) -> PromptParts | None:
        """
        Parse request body into structured PromptParts.
        Returns None if body is not valid JSON or not a messages API request.
        """
        try:
            data = json.loads(body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        if "messages" not in data:
            return None

        # Extract system blocks
        system = data.get("system", [])
        if isinstance(system, str):
            system = [{"type": "text", "text": system}]
        elif not isinstance(system, list):
            system = []

        # Remove existing cache_control markers before reclassifying
        # (we'll re-apply them correctly in build())
        clean_system = []
        for blk in system:
            if isinstance(blk, dict) and "cache_control" in blk:
                blk = {k: v for k, v in blk.items() if k != "cache_control"}
            clean_system.append(blk)

        stable_blocks, volatile_blocks = classify_system_blocks(clean_system)

        # Other fields (model, max_tokens, temperature, etc.)
        other = {k: v for k, v in data.items() if k not in ("system", "tools", "messages")}

        return PromptParts(
            stable_blocks=stable_blocks,
            volatile_blocks=volatile_blocks,
            tools=data.get("tools", []),
            messages=data.get("messages", []),
            other_fields=other,
        )

    def build(self, parts: PromptParts) -> bytes:
        """
        Assemble PromptParts into body bytes with correct cache_control placement.
        """
        body = parts.to_request_body()

        # Apply cache_control to last stable block
        system = body.get("system", [])
        if isinstance(system, list) and system:
            # Find boundary: last stable block (before any volatile blocks)
            n_stable = len(parts.stable_blocks)
            if n_stable > 0:
                # Mark last stable block
                last_stable_idx = n_stable - 1
                blk = dict(system[last_stable_idx])
                if blk.get("type") == "text":
                    blk["cache_control"] = {"type": "ephemeral"}
                    system = list(system)
                    system[last_stable_idx] = blk
                    body["system"] = system
        elif isinstance(system, str) and system.strip():
            body["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        return json.dumps(body, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# PromptCacheStats — per-session tracking
# ---------------------------------------------------------------------------


class PromptCacheStats:
    """Thread-safe per-session cache placement statistics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cache_markers_applied: int = 0
        self.cache_markers_skipped: int = 0  # body had no system prompt
        self.already_marked: int = 0  # idempotent skip
        self.volatile_blocks_found: int = 0
        self.stable_blocks_found: int = 0
        self._started_at: float = time.time()

    def record_applied(
        self,
        stable: int = 0,
        volatile: int = 0,
    ) -> None:
        with self._lock:
            self.cache_markers_applied += 1
            self.stable_blocks_found += stable
            self.volatile_blocks_found += volatile

    def record_skipped(self, already_marked: bool = False) -> None:
        with self._lock:
            if already_marked:
                self.already_marked += 1
            else:
                self.cache_markers_skipped += 1

    def summary(self) -> dict:
        with self._lock:
            total = self.cache_markers_applied + self.cache_markers_skipped
            pct = (self.cache_markers_applied / total * 100) if total > 0 else 0
            return {
                "cache_marked_pct": round(pct, 1),
                "applied": self.cache_markers_applied,
                "skipped_no_system": self.cache_markers_skipped,
                "skipped_already_marked": self.already_marked,
                "avg_stable_blocks": (
                    round(self.stable_blocks_found / self.cache_markers_applied, 1)
                    if self.cache_markers_applied > 0
                    else 0
                ),
                "avg_volatile_blocks": (
                    round(self.volatile_blocks_found / self.cache_markers_applied, 1)
                    if self.cache_markers_applied > 0
                    else 0
                ),
                "uptime_s": round(time.time() - self._started_at),
            }


# Module-level stats singleton
_stats = PromptCacheStats()


def get_stats() -> PromptCacheStats:
    """Return the module-level PromptCacheStats singleton."""
    return _stats


# ---------------------------------------------------------------------------
# High-level helpers: build_stable_prefix / build_volatile_tail
# ---------------------------------------------------------------------------

_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_SHORT_HEX_PATTERN = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)


def _strip_volatile_content(text: str) -> str:
    """Remove known-volatile patterns from text to produce a stable key."""
    for pat in _VOLATILE_PATTERNS:
        text = pat.sub("", text)
    # Strip UUIDs
    text = _UUID_PATTERN.sub("", text)
    text = _SHORT_HEX_PATTERN.sub("", text)
    return text


def build_stable_prefix(system: str, tools: list[dict[str, Any]]) -> str:
    """
    Build a stable, cache-friendly representation of the system prompt + tools.

    The returned string:
    - Is identical (byte-for-byte) for identical inputs
    - Excludes timestamps, UUIDs, and any other volatile patterns
    - Incorporates a deterministically serialized snapshot of tools

    This is the string that should be used as the cache key / prefix for
    Anthropic prompt-caching purposes.

    Parameters
    ----------
    system:
        The raw system prompt string (may contain dynamic content — it will
        be stripped automatically).
    tools:
        List of tool schema dicts.  Serialized deterministically (sorted by name,
        recursive key sort) and appended to the prefix.

    Returns
    -------
    str
        The stable prefix string.

    Examples
    --------
    >>> prefix1 = build_stable_prefix("You are an AI.", [])
    >>> prefix2 = build_stable_prefix("You are an AI.", [])
    >>> prefix1 == prefix2
    True
    """
    # Strip volatile content from system prompt
    clean_system = _strip_volatile_content(system)

    # Serialize tools deterministically (sorted by name, recursive key sort)
    def _sort_keys(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _sort_keys(v) for k, v in sorted(obj.items())}
        if isinstance(obj, list):
            return [_sort_keys(i) for i in obj]
        return obj

    sorted_tools = sorted([_sort_keys(t) for t in tools], key=lambda t: t.get("name", ""))
    tools_str = json.dumps(sorted_tools, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    return f"<stable_prefix>\n{clean_system}\n<tools>\n{tools_str}\n</tools>\n</stable_prefix>"


def build_volatile_tail(
    user_message: str,
    retrieved: list,
    max_tokens: int | None = None,
) -> str:
    """
    Build the volatile tail containing dynamic per-request content.

    The tail has two fixed sections (in order):
      1. ``## Retrieved Context`` — injected retrieval results (if any)
      2. ``## User Message`` — the user's message

    This order ensures the retrieval section always precedes the user message,
    making section positions predictable for parsing.

    Parameters
    ----------
    user_message:
        The user's raw message text.
    retrieved:
        List of retrieved documents.  Each item may be a string or a dict with
        a ``"text"`` key.
    max_tokens:
        Optional token cap for retrieved content.  Approximately 4 chars per
        token.  Truncates retrieved items if the combined text exceeds the cap.

    Returns
    -------
    str
        The assembled volatile tail string.

    Examples
    --------
    >>> tail = build_volatile_tail("Hello", ["doc1"])
    >>> "## Retrieved Context" in tail
    True
    >>> "## User Message" in tail
    True
    >>> "Hello" in tail
    True
    """
    # Build retrieved context section
    retrieved_parts: list[str] = []
    char_budget = max_tokens * 4 if max_tokens is not None else None

    for item in retrieved:
        text = item["text"] if isinstance(item, dict) else str(item)
        if char_budget is not None:
            if char_budget <= 0:
                break
            text = text[:char_budget]
            char_budget -= len(text)
        retrieved_parts.append(text)

    retrieved_section = "## Retrieved Context\n" + "\n\n".join(retrieved_parts)
    user_section = f"## User Message\n{user_message}"

    return f"{retrieved_section}\n\n{user_section}"


__all__ = [
    "PromptBuilder",
    "PromptParts",
    "PromptCacheStats",
    "apply_stable_cache_control",
    "inject_with_cache_boundary",
    "classify_system_blocks",
    "build_stable_prefix",
    "build_volatile_tail",
    "get_stats",
]
