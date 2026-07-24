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
"""

from __future__ import annotations

import collections
import json
import sqlite3
import threading
from typing import Any, List, NamedTuple, Optional

# ---------------------------------------------------------------------------
# Event representation
# ---------------------------------------------------------------------------


class CacheInvalidatorEvent(NamedTuple):
    event_type: str  # tools_changed | system_changed | thinking_mode_changed | ...
    before_value: str  # JSON or summary string
    after_value: str


# ---------------------------------------------------------------------------
# In-memory LRU session body cache
# Cap: 100 sessions. Oldest session evicted on overflow.
# ---------------------------------------------------------------------------


class _SessionBodyCache:
    """Thread-safe in-memory LRU cache of last-seen request body per session_id."""

    def __init__(self, maxsize: int = 100):
        self._maxsize = maxsize
        self._cache: collections.OrderedDict[str, bytes] = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Optional[bytes]:
        """Return the previously stored body for session_id, or None."""
        with self._lock:
            if session_id not in self._cache:
                return None
            self._cache.move_to_end(session_id)
            return self._cache[session_id]

    def put(self, session_id: str, body: bytes) -> None:
        """Store body for session_id, evicting the oldest entry if at capacity."""
        with self._lock:
            if session_id in self._cache:
                self._cache.move_to_end(session_id)
            else:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)  # evict LRU (oldest)
            self._cache[session_id] = body

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
    """
    events: List[CacheInvalidatorEvent] = []

    try:
        prev = json.loads(prev_body)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return events
    try:
        curr = json.loads(curr_body)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return events

    # ── tools_changed ──────────────────────────────────────────────────────
    prev_tools = _canonical_tools(prev.get("tools", []))
    curr_tools = _canonical_tools(curr.get("tools", []))
    if prev_tools != curr_tools:
        events.append(CacheInvalidatorEvent("tools_changed", prev_tools, curr_tools))

    # ── system_changed ─────────────────────────────────────────────────────
    prev_system = json.dumps(prev.get("system", ""), sort_keys=True, ensure_ascii=False)
    curr_system = json.dumps(curr.get("system", ""), sort_keys=True, ensure_ascii=False)
    if prev_system != curr_system:
        events.append(CacheInvalidatorEvent("system_changed", prev_system, curr_system))

    # ── thinking_mode_changed ──────────────────────────────────────────────
    prev_thinking = prev.get("thinking")
    curr_thinking = curr.get("thinking")
    prev_thinking_str = (
        json.dumps(prev_thinking, sort_keys=True, ensure_ascii=False)
        if prev_thinking is not None
        else "null"
    )
    curr_thinking_str = (
        json.dumps(curr_thinking, sort_keys=True, ensure_ascii=False)
        if curr_thinking is not None
        else "null"
    )
    if prev_thinking_str != curr_thinking_str:
        events.append(
            CacheInvalidatorEvent("thinking_mode_changed", prev_thinking_str, curr_thinking_str)
        )

    # ── tool_choice_changed ────────────────────────────────────────────────
    prev_tc = json.dumps(prev.get("tool_choice"), sort_keys=True, ensure_ascii=False)
    curr_tc = json.dumps(curr.get("tool_choice"), sort_keys=True, ensure_ascii=False)
    if prev_tc != curr_tc:
        events.append(CacheInvalidatorEvent("tool_choice_changed", prev_tc, curr_tc))

    # ── image_mode_changed ─────────────────────────────────────────────────
    prev_has_images = _has_images(prev.get("messages", []))
    curr_has_images = _has_images(curr.get("messages", []))
    if prev_has_images != curr_has_images:
        events.append(
            CacheInvalidatorEvent(
                "image_mode_changed",
                "has_images" if prev_has_images else "no_images",
                "has_images" if curr_has_images else "no_images",
            )
        )

    return events


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
        conn.close()
    except Exception:
        pass  # fail-open: never break a request over telemetry writes
