"""
Cache invalidator detector (log-only).

Detects per-session request changes that would invalidate Claude's prompt cache:
  - tools_changed
  - system_changed
  - thinking_mode_changed
  - tool_choice_changed
  - image_mode_changed

Records events in cache_invalidator_events table. Log-only in Phase 2.
Phase 3 will consume these events to take corrective action.

Design decision: in-memory LRU keyed on session_id (cap=100 sessions).
Rationale: storing full request bodies in the requests table would require a
large schema change (blob column) and add significant storage overhead. The
in-memory cache is volatile (lost on restart) which is acceptable for Phase 2
telemetry — the goal is validating the detector against real Claude Code
traffic, not guaranteeing 100% coverage across every restart edge case. LRU
cap of 100 sessions prevents unbounded memory growth; oldest session is evicted
when the cap is reached.

The cache holds a compact signature of each session's last request (the
canonical tools, system, thinking and tool_choice values and whether images
are present), not the raw body, so each request body is parsed once rather
than the previous body being parsed again on every POST. Entries are also
bounded by total size: ``TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES`` (default
32 MiB) evicts the least recently used sessions first.
"""

from __future__ import annotations

import collections
import json
import os
import sqlite3
import threading
from typing import Any, List, NamedTuple, Optional, Union

# ---------------------------------------------------------------------------
# Event representation
# ---------------------------------------------------------------------------


class CacheInvalidatorEvent(NamedTuple):
    event_type: str  # tools_changed | system_changed | thinking_mode_changed | ...
    before_value: str  # JSON or summary string
    after_value: str


# ---------------------------------------------------------------------------
# In-memory LRU session cache
# Caps: 100 sessions and TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES of entries.
# ---------------------------------------------------------------------------

DEFAULT_CACHE_MAX_BYTES = 32 * 1024 * 1024


def _cache_max_bytes() -> int:
    raw = os.environ.get("TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES", "")
    try:
        value = int(raw) if raw else DEFAULT_CACHE_MAX_BYTES
    except ValueError:
        return DEFAULT_CACHE_MAX_BYTES
    return max(0, value)


class _BodySignature(NamedTuple):
    """The request fields the detector compares, in canonical form."""

    tools: str
    system: str
    thinking: str
    tool_choice: str
    has_images: bool

    @property
    def nbytes(self) -> int:
        return len(self.tools) + len(self.system) + len(self.thinking) + len(self.tool_choice)


_CacheEntry = Union[bytes, _BodySignature, None]


def _entry_size(entry: _CacheEntry) -> int:
    if entry is None:
        return 0
    if isinstance(entry, _BodySignature):
        return entry.nbytes
    return len(entry)


class _SessionBodyCache:
    """Thread-safe in-memory LRU cache of the last request seen per session_id.

    Bounded by entry count (``maxsize``) and by total entry size
    (``max_bytes``); the least recently used session is evicted first, and an
    entry larger than ``max_bytes`` on its own is not kept.
    """

    def __init__(self, maxsize: int = 100, max_bytes: Optional[int] = None):
        self._maxsize = maxsize
        self._max_bytes = _cache_max_bytes() if max_bytes is None else max(0, max_bytes)
        self._bytes = 0
        self._cache: collections.OrderedDict[str, _CacheEntry] = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> _CacheEntry:
        """Return the stored entry for session_id, or None."""
        with self._lock:
            if session_id not in self._cache:
                return None
            self._cache.move_to_end(session_id)
            return self._cache[session_id]

    def put(self, session_id: str, body: _CacheEntry) -> None:
        """Store the entry for session_id, evicting least recently used ones."""
        size = _entry_size(body)
        with self._lock:
            if session_id in self._cache:
                self._bytes -= _entry_size(self._cache.pop(session_id))
            elif len(self._cache) >= self._maxsize:
                _, evicted = self._cache.popitem(last=False)  # evict LRU (oldest)
                self._bytes -= _entry_size(evicted)
            if size > self._max_bytes:
                return
            self._cache[session_id] = body
            self._bytes += size
            while self._bytes > self._max_bytes:
                _, evicted = self._cache.popitem(last=False)
                self._bytes -= _entry_size(evicted)

    @property
    def nbytes(self) -> int:
        with self._lock:
            return self._bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


_SESSION_CACHE: Optional[_SessionBodyCache] = None
_SESSION_CACHE_LOCK = threading.Lock()


def _get_session_cache() -> _SessionBodyCache:
    """Return the module-level singleton session body cache."""
    global _SESSION_CACHE
    if _SESSION_CACHE is None:
        with _SESSION_CACHE_LOCK:
            if _SESSION_CACHE is None:
                _SESSION_CACHE = _SessionBodyCache(maxsize=100)
    return _SESSION_CACHE


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------


def _canonical_tools(tools: Any) -> str:
    """Normalize tools array to a canonical JSON string for stable comparison.

    Sorts by tool name so insertion-order differences don't produce false positives.
    """
    if not tools:
        return "[]"
    try:
        normalized = sorted(
            tools,
            key=lambda t: t.get("name", "") if isinstance(t, dict) else str(t),
        )
        return json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    except Exception:
        return json.dumps(tools, sort_keys=True, ensure_ascii=False)


def _has_images(messages: Any) -> bool:
    """Return True if any message content block has type 'image'."""
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
    return False


# ---------------------------------------------------------------------------
# Main detector
# ---------------------------------------------------------------------------


def _body_signature(body: Union[bytes, str]) -> Optional[_BodySignature]:
    """Parse a request body once into the fields the detector compares.

    Returns None when the body is not a JSON object.
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    thinking = parsed.get("thinking")
    return _BodySignature(
        tools=_canonical_tools(parsed.get("tools", [])),
        system=json.dumps(parsed.get("system", ""), sort_keys=True, ensure_ascii=False),
        thinking=(
            json.dumps(thinking, sort_keys=True, ensure_ascii=False)
            if thinking is not None
            else "null"
        ),
        tool_choice=json.dumps(parsed.get("tool_choice"), sort_keys=True, ensure_ascii=False),
        has_images=_has_images(parsed.get("messages", [])),
    )


def _diff_signatures(
    prev: _BodySignature,
    curr: _BodySignature,
) -> List[CacheInvalidatorEvent]:
    """Events for the cache-relevant fields that differ between two requests."""
    events: List[CacheInvalidatorEvent] = []
    # ── tools_changed ──────────────────────────────────────────────────────
    if prev.tools != curr.tools:
        events.append(CacheInvalidatorEvent("tools_changed", prev.tools, curr.tools))
    # ── system_changed ─────────────────────────────────────────────────────
    if prev.system != curr.system:
        events.append(CacheInvalidatorEvent("system_changed", prev.system, curr.system))
    # ── thinking_mode_changed ──────────────────────────────────────────────
    if prev.thinking != curr.thinking:
        events.append(CacheInvalidatorEvent("thinking_mode_changed", prev.thinking, curr.thinking))
    # ── tool_choice_changed ────────────────────────────────────────────────
    if prev.tool_choice != curr.tool_choice:
        events.append(
            CacheInvalidatorEvent("tool_choice_changed", prev.tool_choice, curr.tool_choice)
        )
    # ── image_mode_changed ─────────────────────────────────────────────────
    if prev.has_images != curr.has_images:
        events.append(
            CacheInvalidatorEvent(
                "image_mode_changed",
                "has_images" if prev.has_images else "no_images",
                "has_images" if curr.has_images else "no_images",
            )
        )
    return events


def _detect_cache_invalidators(
    prev_body: bytes,
    curr_body: bytes,
) -> List[CacheInvalidatorEvent]:
    """Compare two consecutive request bodies and return cache invalidator events.

    Returns a list of CacheInvalidatorEvent(event_type, before_value, after_value).
    Empty list means no invalidators detected.

    False positives are acceptable in Phase 2 (log-only); false negatives are NOT
    — every real invalidator must be caught.

    Event types:
        tools_changed           — tools array differs (count, names, or schemas)
        system_changed          — system block differs
        thinking_mode_changed   — thinking presence/absence or budget_tokens changed
        tool_choice_changed     — tool_choice differs
        image_mode_changed      — image content blocks added or removed

    The proxy keeps each session's signature (``_body_signature``) and calls
    ``_diff_signatures`` directly, so it parses each body only once.
    """
    prev = _body_signature(prev_body)
    if prev is None:
        return []
    curr = _body_signature(curr_body)
    if curr is None:
        return []
    return _diff_signatures(prev, curr)


# ---------------------------------------------------------------------------
# DB write helper
# ---------------------------------------------------------------------------


def _write_cache_invalidator_events(
    db_path: str,
    request_id: Any,
    session_id: str,
    events: List[CacheInvalidatorEvent],
) -> None:
    """Write detected cache invalidator events to the cache_invalidator_events table.

    Fail-open: exceptions are swallowed so a DB write failure never breaks a request.
    """
    if not events:
        return
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        for event in events:
            conn.execute(
                "INSERT INTO cache_invalidator_events "
                "(request_id, session_id, timestamp, event_type, before_value, after_value) "
                "VALUES (?, ?, datetime('now'), ?, ?, ?)",
                (
                    request_id,
                    session_id,
                    event.event_type,
                    event.before_value,
                    event.after_value,
                ),
            )
        conn.commit()
    except Exception:
        pass  # fail-open: never break a request over telemetry writes
    finally:
        if conn is not None:
            conn.close()
