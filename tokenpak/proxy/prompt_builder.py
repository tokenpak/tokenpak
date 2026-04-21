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

    from tokenpak.proxy.prompt_builder import apply_stable_cache_control

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


def _mark_message_content_cacheable(message: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Mark the last text content block of a message as cacheable."""
    marked = False
    msg = dict(message)
    content = msg.get("content")

    if isinstance(content, str):
        if content.strip():
            msg["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
            marked = True
        return msg, marked

    if not isinstance(content, list):
        return msg, False

    content_out = [dict(c) if isinstance(c, dict) else c for c in content]
    for i in range(len(content_out) - 1, -1, -1):
        block = content_out[i]
        if isinstance(block, dict) and block.get("type") == "text":
            if block.get("cache_control") != {"type": "ephemeral"}:
                content_out[i] = dict(block, cache_control={"type": "ephemeral"})
                marked = True
            msg["content"] = content_out
            return msg, marked

    return msg, False


def apply_stable_cache_control(body_bytes: bytes) -> bytes:
    """Backward-compatible entrypoint for deterministic cache breakpoints."""
    return apply_deterministic_cache_breakpoints(body_bytes)


def apply_deterministic_cache_breakpoints(body_bytes: bytes) -> bytes:
    """Apply deterministic multi-breakpoint cache markers to Anthropic requests."""
    try:
        data = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body_bytes

    changed = False

    # Breakpoint 1: last stable system block
    system = data.get("system")
    if isinstance(system, str):
        if system.strip():
            data["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
            changed = True
            _stats.record_breakpoint("system_last", True)
        else:
            _stats.record_breakpoint("system_last", False)
    elif isinstance(system, list) and system:
        stable_blocks, volatile_blocks = classify_system_blocks(system)
        if stable_blocks:
            marked_stable = _mark_last_block_cacheable(stable_blocks)
            rebuilt = marked_stable + volatile_blocks
            if rebuilt != system:
                data["system"] = rebuilt
                changed = True
                _stats.record_breakpoint("system_last", True)
            else:
                _stats.record_breakpoint("system_last", False)
        else:
            _stats.record_breakpoint("system_last", False)
    else:
        _stats.record_breakpoint("system_last", False)

    # Breakpoint 2: last tool definition block
    tools = data.get("tools")
    if isinstance(tools, list) and tools:
        tools_out = [dict(t) if isinstance(t, dict) else t for t in tools]
        idx = len(tools_out) - 1
        last_tool = tools_out[idx]
        if isinstance(last_tool, dict):
            if last_tool.get("cache_control") != {"type": "ephemeral"}:
                tools_out[idx] = dict(last_tool, cache_control={"type": "ephemeral"})
                data["tools"] = tools_out
                changed = True
                _stats.record_breakpoint("tools_last", True)
            else:
                _stats.record_breakpoint("tools_last", False)
        else:
            _stats.record_breakpoint("tools_last", False)
    else:
        _stats.record_breakpoint("tools_last", False)

    # Breakpoints 3 & 4: conversation midpoint and second-to-last assistant
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        messages_out = [dict(m) if isinstance(m, dict) else m for m in messages]

        midpoint_idx = len(messages_out) // 2
        if isinstance(messages_out[midpoint_idx], dict):
            new_msg, did_mark = _mark_message_content_cacheable(messages_out[midpoint_idx])
            if did_mark:
                messages_out[midpoint_idx] = new_msg
                changed = True
            _stats.record_breakpoint("conversation_midpoint", did_mark)
        else:
            _stats.record_breakpoint("conversation_midpoint", False)

        assistant_indices = [
            i
            for i, m in enumerate(messages_out)
            if isinstance(m, dict) and m.get("role") == "assistant"
        ]
        if len(assistant_indices) >= 2:
            target_idx = assistant_indices[-2]
            new_msg, did_mark = _mark_message_content_cacheable(messages_out[target_idx])
            if did_mark:
                messages_out[target_idx] = new_msg
                changed = True
            _stats.record_breakpoint("assistant_second_last", did_mark)
        else:
            _stats.record_breakpoint("assistant_second_last", False)

        if changed:
            data["messages"] = messages_out
    else:
        _stats.record_breakpoint("conversation_midpoint", False)
        _stats.record_breakpoint("assistant_second_last", False)

    if not changed:
        return body_bytes

    # --- Cap total cache_control blocks to Anthropic max (4) ---
    _all_cc = []
    _sys = data.get("system", [])
    if isinstance(_sys, list):
        for _si, _sb in enumerate(_sys):
            if isinstance(_sb, dict) and "cache_control" in _sb:
                _all_cc.append(("system", _si))
    _tools = data.get("tools", [])
    if isinstance(_tools, list):
        for _ti, _tb in enumerate(_tools):
            if isinstance(_tb, dict) and "cache_control" in _tb:
                _all_cc.append(("tools", _ti))
    _msgs = data.get("messages", [])
    if isinstance(_msgs, list):
        for _mi, _mm in enumerate(_msgs):
            _mc = _mm.get("content", []) if isinstance(_mm, dict) else []
            if isinstance(_mc, list):
                for _ci, _cb in enumerate(_mc):
                    if isinstance(_cb, dict) and "cache_control" in _cb:
                        _all_cc.append(("messages", _mi, _ci))
    if len(_all_cc) > 4:
        for _loc in _all_cc[:-4]:
            if _loc[0] == "system":
                data["system"][_loc[1]].pop("cache_control", None)
            elif _loc[0] == "tools":
                data["tools"][_loc[1]].pop("cache_control", None)
            elif _loc[0] == "messages":
                data["messages"][_loc[1]]["content"][_loc[2]].pop("cache_control", None)

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
        self.breakpoint_applied: dict[str, int] = {
            "system_last": 0,
            "tools_last": 0,
            "conversation_midpoint": 0,
            "assistant_second_last": 0,
        }
        self.breakpoint_skipped: dict[str, int] = {
            "system_last": 0,
            "tools_last": 0,
            "conversation_midpoint": 0,
            "assistant_second_last": 0,
        }
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

    def record_breakpoint(self, name: str, applied: bool) -> None:
        with self._lock:
            if name not in self.breakpoint_applied:
                return
            if applied:
                self.breakpoint_applied[name] += 1
            else:
                self.breakpoint_skipped[name] += 1

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
                "breakpoint_activity": {
                    "applied": dict(self.breakpoint_applied),
                    "skipped": dict(self.breakpoint_skipped),
                },
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


# ---------------------------------------------------------------------------
# DeterministicPromptPack — Fixed Section Ordering & Byte-Identical Output
# ---------------------------------------------------------------------------


@dataclass
class DeterministicPromptPack:
    """
    Enforces deterministic prompt assembly with fixed section ordering.

    Canonical Section Order
    -----------------------
    1. SYSTEM PROMPT          — model behavior, instructions (STABLE)
    2. TOOLS DEFINITIONS      — tool schemas in canonical order (STABLE)
    3. POLICIES/CONSTRAINTS   — safety rules, guardrails (STABLE)
    4. RETRIEVED CONTEXT      — search results, injected knowledge (VOLATILE)
    5. USER INPUT             — user message / query (VOLATILE)

    Key Properties
    ~~~~~~~~~~~~~~
    - **Fixed ordering** ensures consistent section positioning
    - **Deterministic separators** (no extra whitespace, canonical line breaks)
    - **Byte-identical output** for equivalent inputs (proven by tests)
    - **Stable vs volatile boundary** explicitly marked for cache_control
    - **Optional integration** — does not break existing PromptBuilder

    Why This Matters
    ~~~~~~~~~~~~~~~~
    Prompt caching in Anthropic APIs requires:
      1. Consistent prefix structure (cache key must be stable)
      2. Volatile content segregated after the cache boundary
      3. Deterministic encoding to ensure byte-for-byte matches

    Without fixed ordering:
      - Section position varies based on assembly order
      - Cache keys are unstable even for semantically identical prompts
      - Equivalent requests produce different byte sequences

    With DeterministicPromptPack:
      - Section position is guaranteed
      - Cache keys are stable and reproducible
      - Byte-identical output for identical inputs

    Usage Example (Before)
    ~~~~~~~~~~~~~~~~~~~~~~
    ```python
    # Old way: sections assembled ad-hoc, order inconsistent
    system_parts = []
    system_parts.append(system_prompt)
    if tools:
        system_parts.append(json.dumps(tools))
    if policies:
        system_parts.append(policies)
    if vault_context:
        system_parts.append(vault_context)

    system_str = "\\n\\n".join(system_parts)  # order may vary, spacing inconsistent
    ```

    Usage Example (After)
    ~~~~~~~~~~~~~~~~~~~~~
    ```python
    pack = DeterministicPromptPack(
        system="You are a helpful AI.",
        tools=[{"name": "search", "description": "..."}],
        policies="Always be honest.",
        retrieved_context=["doc1", "doc2"],
        user_input="What is X?",
    )

    system_block = pack.to_system_block()
    # Output: deterministic, section order fixed, byte-identical for same inputs
    ```

    Attributes
    ~~~~~~~~~~
    system : str
        System prompt / instructions for the model. (STABLE)
    tools : list[dict]
        Tool schemas (typically frozen by tool_schema_registry). (STABLE)
    policies : str
        Safety rules, constraints, guardrails. (STABLE)
    retrieved_context : list[str | dict]
        Retrieved documents or search results. (VOLATILE — changes per request)
    user_input : str
        User message or query. (VOLATILE — changes per request)
    metadata : dict
        Optional metadata (not included in output, useful for debugging).

    Cache Boundary Marking
    ~~~~~~~~~~~~~~~~~~~~~~
    The last stable section (policies or tools, whichever is last) is marked
    with cache_control: {type: "ephemeral"}. Volatile sections follow without
    cache markers.

    Before::

        system: [
            {type: text, text: "SYSTEM..."},
            {type: text, text: "TOOLS..."},
            {type: text, text: "POLICIES..."}  ← cache boundary
            {type: text, text: "RETRIEVED..."},  ← volatile, no marker
            {type: text, text: "USER..."}        ← volatile, no marker
        ]

    After::

        system: [
            {type: text, text: "SYSTEM..."},
            {type: text, text: "TOOLS..."},
            {type: text, text: "POLICIES...", cache_control: {type: ephemeral}}
            {type: text, text: "RETRIEVED..."},
            {type: text, text: "USER..."}
        ]

    Testing & Validation
    ~~~~~~~~~~~~~~~~~~~~
    Byte-identity is proven via:

        pack1 = DeterministicPromptPack(...same inputs...)
        pack2 = DeterministicPromptPack(...same inputs...)
        assert pack1.to_request_body() == pack2.to_request_body()
        assert pack1.to_system_block() == pack2.to_system_block()
        # byte-for-byte identical JSON output

    Integration Guidance
    ~~~~~~~~~~~~~~~~~~~~
    1. **In proxy layer**: Replace ad-hoc system string assembly with:
       ```
       pack = DeterministicPromptPack(
           system=read_system_prompt(),
           tools=registry.get_tools(),
           policies=read_policies(),
           retrieved_context=vault_search(...),
           user_input=msg.content,
       )
       body["system"] = pack.to_system_block()
       ```

    2. **With cache_control**: Automatically handled:
       ```
       blocks = pack.to_system_block()
       # Last stable block already has cache_control marker
       ```

    3. **Feature-flagged**: Disable with env var or config:
       ```
       if config.USE_DETERMINISTIC_PACKING:
           pack = DeterministicPromptPack(...)
           body["system"] = pack.to_system_block()
       else:
           # Fallback to PromptBuilder or legacy assembly
       ```
    """

    system: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    policies: str = ""
    retrieved_context: list[str | dict[str, Any]] = field(default_factory=list)
    user_input: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- Canonical formatting (internal) ---
    _SECTION_SEPARATOR = "\n\n"  # Always 2 newlines between sections
    _STABLE_SECTIONS = ["system", "tools", "policies"]
    _VOLATILE_SECTIONS = ["retrieved_context", "user_input"]

    def _serialize_tools(self) -> str:
        """
        Serialize tools deterministically.

        Order:
          1. Sort tools by name
          2. Recursively sort all dict keys
          3. Use compact JSON (no spaces)
          4. Ensure unicode is consistent

        This ensures byte-identity for identical tool lists.
        """
        if not self.tools:
            return ""

        def _sort_keys(obj: Any) -> Any:
            """Recursively sort dict keys for deterministic JSON."""
            if isinstance(obj, dict):
                return {k: _sort_keys(v) for k, v in sorted(obj.items())}
            if isinstance(obj, list):
                return [_sort_keys(i) for i in obj]
            return obj

        # Sort tools by name, then recursively sort keys
        sorted_tools = sorted(
            [_sort_keys(t) for t in self.tools],
            key=lambda t: t.get("name", ""),
        )

        # Compact JSON: no spaces after separators
        return json.dumps(
            sorted_tools,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _serialize_retrieved_context(self) -> str:
        """
        Serialize retrieved context deterministically.

        Each item is extracted as text, then joined with newlines.
        Order is preserved (assumes input is already ranked/sorted).
        """
        if not self.retrieved_context:
            return ""

        parts = []
        for item in self.retrieved_context:
            if isinstance(item, dict):
                # Support {text, source, score, ...} format
                text = item.get("text", str(item))
            else:
                text = str(item)
            parts.append(text)

        return "\n".join(parts)

    def _build_stable_block(self) -> str:
        """
        Build the stable section (SYSTEM + TOOLS + POLICIES).

        These sections never change per-request, so they're cacheable.
        Returns a single string suitable for the first system block.
        """
        parts: list[str] = []

        if self.system:
            parts.append(f"# SYSTEM\n\n{self.system}")

        tools_json = self._serialize_tools()
        if tools_json:
            parts.append(f"# TOOLS\n\n{tools_json}")

        if self.policies:
            parts.append(f"# POLICIES/CONSTRAINTS\n\n{self.policies}")

        return self._SECTION_SEPARATOR.join(parts)

    def _build_volatile_block(self) -> str:
        """
        Build the volatile section (RETRIEVED CONTEXT + USER INPUT).

        These sections change per-request, so they're not cacheable.
        Returns a single string suitable for append to system blocks.
        """
        parts: list[str] = []

        retrieved_json = self._serialize_retrieved_context()
        if retrieved_json:
            parts.append(f"# RETRIEVED CONTEXT\n\n{retrieved_json}")

        if self.user_input:
            parts.append(f"# USER INPUT\n\n{self.user_input}")

        return self._SECTION_SEPARATOR.join(parts)

    def to_system_block(self) -> list[dict[str, Any]]:
        """
        Assemble into a list of system content blocks (Anthropic format).

        Structure::

            [
                {type: text, text: <stable sections>, cache_control: {type: ephemeral}},
                {type: text, text: <volatile sections>},
            ]

        The last stable block has cache_control marker.
        Volatile blocks do not.

        Returns
        -------
        list[dict]
            Anthropic system content blocks.
        """
        blocks: list[dict[str, Any]] = []

        stable_text = self._build_stable_block()
        if stable_text:
            blocks.append(
                {
                    "type": "text",
                    "text": stable_text,
                    "cache_control": {"type": "ephemeral"},
                }
            )

        volatile_text = self._build_volatile_block()
        if volatile_text:
            blocks.append(
                {
                    "type": "text",
                    "text": volatile_text,
                }
            )

        return blocks

    def to_request_body(self, model: str = "claude-3-5-sonnet-20241022") -> dict[str, Any]:
        """
        Assemble into a complete Anthropic messages API request body.

        Parameters
        ----------
        model : str
            Model identifier (default: claude-3-5-sonnet-20241022)

        Returns
        -------
        dict
            Anthropic request body with system, tools, messages fields.

        Example
        -------
        >>> pack = DeterministicPromptPack(
        ...     system="You are helpful.",
        ...     user_input="Hello!",
        ... )
        >>> body = pack.to_request_body()
        >>> body["system"]
        [{"type": "text", "text": "# SYSTEM\n\nYou are helpful.", ...}]
        >>> body["messages"]
        [{"role": "user", "content": "Hello!"}]
        """
        body: dict[str, Any] = {
            "model": model,
            "system": self.to_system_block(),
            "messages": [
                {
                    "role": "user",
                    "content": self.user_input,
                }
            ],
        }

        if self.tools:
            body["tools"] = self.tools

        return body

    def __eq__(self, other: Any) -> bool:
        """
        Two packs are equal if all their fields are equal.

        This is used for testing byte-identity:
            pack1 = DeterministicPromptPack(...)
            pack2 = DeterministicPromptPack(...)
            assert pack1 == pack2  # all fields identical
            assert pack1.to_system_block() == pack2.to_system_block()  # output identical
        """
        if not isinstance(other, DeterministicPromptPack):
            return False
        return (
            self.system == other.system
            and self.tools == other.tools
            and self.policies == other.policies
            and self.retrieved_context == other.retrieved_context
            and self.user_input == other.user_input
        )

    def __repr__(self) -> str:
        """Pretty repr showing section sizes."""
        return (
            f"DeterministicPromptPack("
            f"system={len(self.system)}B, "
            f"tools={len(self.tools)}, "
            f"policies={len(self.policies)}B, "
            f"retrieved_context={len(self.retrieved_context)}, "
            f"user_input={len(self.user_input)}B"
            f")"
        )


__all__ = [
    "PromptBuilder",
    "PromptParts",
    "PromptCacheStats",
    "DeterministicPromptPack",
    "apply_stable_cache_control",
    "apply_deterministic_cache_breakpoints",
    "inject_with_cache_boundary",
    "classify_system_blocks",
    "build_stable_prefix",
    "build_volatile_tail",
    "get_stats",
]
