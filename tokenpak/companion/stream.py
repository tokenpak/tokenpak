# SPDX-License-Identifier: Apache-2.0
"""
tokenpak.companion.stream — Defensive Truncated-Stream Guard
============================================================

A defensive reader wrapped around a provider's streamed (SSE-style) response.
It does NOT alter, retry, or rewrite the underlying bytes — it only *observes*
the stream as it is consumed and, on detecting a truncated / unterminated
stream, surfaces a clean, structured error to the calling client while
preserving the partial content already received.

Three truncation conditions are detected:

  (a) JSON envelope opened but never closed
      — a top-level ``{`` (or ``[``) was seen but the matching closer never
        arrived before the stream ended.
  (b) ``event: message_stop`` never received before EOF
      — the canonical terminal SSE event for an Anthropic message stream was
        never observed.
  (c) provider connection dropped mid-chunk
      — the byte iterator raised mid-stream (e.g. ``ConnectionError``,
        ``IncompleteRead``) rather than ending cleanly.

On detection the guard:

  1. Emits a structured ``provider.error`` event (``kind=stream_truncated``,
     ``severity=warn``) to the wire-plane ledger (``monitor.db``) with attrs
     ``{bytes_received, last_event_kind, time_since_last_chunk_ms, trace_id}``.
     NO raw provider body content is ever written.
  2. Raises :class:`StreamTruncatedError` carrying the partial content, a
     stable error code ``TPK_STREAM_TRUNCATED``, a remedy hint, and the
     ``trace_id`` for replay.

Plane discipline: this module writes ONLY to ``monitor.db`` (the wire plane).
It never touches the companion journal (``journal.db``).

Feature flag: behaviour is gated behind the ``TPK_STREAM_GUARD`` environment
variable. Default is ON. Set ``TPK_STREAM_GUARD=0`` to pass the stream through
unchanged (no detection, no events, no wrapped error) — a pure passthrough
fallback identical to pre-guard behaviour.

Usage::

    from tokenpak.companion.stream import guarded_stream, StreamTruncatedError

    try:
        for chunk in guarded_stream(provider_byte_iter):
            forward_to_client(chunk)
    except StreamTruncatedError as err:
        # err.partial_content holds bytes received so far
        # err.code == "TPK_STREAM_TRUNCATED"
        # err.trace_id for replay; err.remedy for the user-facing hint
        surface_clean_error(err.to_dict())
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Environment flag controlling the guard. Default ON; "0" => passthrough.
GUARD_ENV = "TPK_STREAM_GUARD"

#: Stable, machine-parseable error code surfaced to the calling client.
STREAM_TRUNCATED_CODE = "TPK_STREAM_TRUNCATED"

#: User-facing remedy hint. No internal references.
STREAM_TRUNCATED_REMEDY = "retry with smaller max_output_tokens or shorter prompt"

#: TIP event name + classification for the emitted telemetry event.
EVENT_NAME = "provider.error"
EVENT_KIND = "stream_truncated"
EVENT_SEVERITY = "warn"

#: Canonical terminal SSE event line for an Anthropic message stream.
_MESSAGE_STOP_MARKER = "message_stop"

#: monitor.db table holding structured provider events (additive — the
#: request ledger table ``requests`` is left untouched).
_EVENTS_TABLE = "provider_events"


def guard_enabled() -> bool:
    """Return True when the stream guard is active.

    ON by default; only the explicit string ``"0"`` disables it. Any other
    value (including unset) keeps the guard on.
    """
    return os.environ.get(GUARD_ENV, "1").strip() != "0"


# ---------------------------------------------------------------------------
# Structured error surfaced to the caller
# ---------------------------------------------------------------------------


class StreamTruncatedError(Exception):
    """Clean, structured error raised when a provider stream is truncated.

    Carries the partial content received so far, a stable error code, a
    remedy hint, and the trace_id for replay. The string form is safe to log
    (it contains the code + trace_id but never raw provider body content).
    """

    code: str = STREAM_TRUNCATED_CODE

    def __init__(
        self,
        *,
        partial_content: bytes,
        trace_id: str,
        reason: str,
        last_event_kind: str = "",
        bytes_received: int = 0,
        time_since_last_chunk_ms: int = 0,
        remedy: str = STREAM_TRUNCATED_REMEDY,
    ) -> None:
        self.partial_content = partial_content
        self.trace_id = trace_id
        self.reason = reason
        self.last_event_kind = last_event_kind
        self.bytes_received = bytes_received
        self.time_since_last_chunk_ms = time_since_last_chunk_ms
        self.remedy = remedy
        super().__init__(
            f"{self.code}: provider stream truncated ({reason}); "
            f"trace_id={trace_id}; remedy={remedy}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe error envelope for the calling client.

        ``partial_content`` is decoded leniently to text so it can be shown to
        the user. The byte form remains available on the exception instance.
        """
        return {
            "error": {
                "code": self.code,
                "message": "Provider stream ended before completion.",
                "reason": self.reason,
                "remedy": self.remedy,
                "trace_id": self.trace_id,
                "partial_content": self.partial_content.decode("utf-8", "replace"),
            }
        }


# ---------------------------------------------------------------------------
# Stream-state tracking
# ---------------------------------------------------------------------------


@dataclass
class _StreamState:
    """Mutable accounting carried while the guard consumes a stream."""

    trace_id: str
    buffer: bytearray = field(default_factory=bytearray)
    bytes_received: int = 0
    last_event_kind: str = ""
    saw_message_stop: bool = False
    json_depth: int = 0
    json_opened: bool = False
    in_string: bool = False
    escaped: bool = False
    last_chunk_at: float = field(default_factory=time.monotonic)
    connection_dropped: bool = False
    drop_detail: str = ""

    def note_chunk(self, chunk: bytes) -> None:
        """Update accounting from a freshly received chunk."""
        self.last_chunk_at = time.monotonic()
        self.bytes_received += len(chunk)
        self.buffer.extend(chunk)
        self._scan_json(chunk)
        self._scan_events(chunk)

    # ---- JSON envelope balance (condition a) -------------------------------

    def _scan_json(self, chunk: bytes) -> None:
        """Track top-level JSON brace/bracket balance, string-aware.

        Detects whether a JSON envelope was opened. ``json_depth`` returning
        to 0 after having opened means a balanced (closed) envelope; a
        positive depth at EOF means an unterminated envelope.
        """
        for byte in chunk:
            ch = chr(byte)
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif ch == "\\":
                    self.escaped = True
                elif ch == '"':
                    self.in_string = False
                continue
            if ch == '"':
                self.in_string = True
            elif ch in "{[":
                self.json_depth += 1
                self.json_opened = True
            elif ch in "}]":
                if self.json_depth > 0:
                    self.json_depth -= 1

    @property
    def json_envelope_unterminated(self) -> bool:
        """True when a JSON envelope was opened but never balanced/closed."""
        return self.json_opened and (self.json_depth > 0 or self.in_string)

    # ---- SSE event tracking (condition b) ----------------------------------

    def _scan_events(self, chunk: bytes) -> None:
        """Track the last SSE ``event:`` line and the terminal message_stop.

        Decoded leniently; we only read structural ``event:`` markers, never
        retain body content.
        """
        text = chunk.decode("utf-8", "ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("event:"):
                kind = stripped[len("event:") :].strip()
                if kind:
                    self.last_event_kind = kind
                    if kind == _MESSAGE_STOP_MARKER:
                        self.saw_message_stop = True

    def time_since_last_chunk_ms(self) -> int:
        return int((time.monotonic() - self.last_chunk_at) * 1000)


def _looks_like_sse(buffer: bytes) -> bool:
    """Heuristic: does this stream carry SSE ``event:`` framing at all?

    The ``message_stop`` check (condition b) only applies to SSE-framed
    streams. A non-SSE byte stream (e.g. a single JSON body) is judged purely
    by the JSON-envelope balance check, avoiding false positives.
    """
    return b"event:" in buffer


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


def guarded_stream(
    source: Iterable[bytes],
    *,
    trace_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Iterator[bytes]:
    """Wrap *source* (an iterable of byte chunks) with truncation detection.

    Yields each chunk unchanged as it is read (byte-for-byte passthrough). On
    a clean end-of-stream the generator simply stops. On a detected
    truncation it emits a ``provider.error`` event to ``monitor.db`` and
    raises :class:`StreamTruncatedError` carrying the partial content.

    When the guard is disabled (``TPK_STREAM_GUARD=0``) this is a pure
    passthrough: chunks are yielded with no detection, no event, no wrapped
    error.

    Args:
        source: Iterable yielding ``bytes`` chunks from the provider.
        trace_id: Optional stable trace id; generated if omitted.
        db_path: Optional explicit monitor.db path (testing/override).

    Yields:
        Each chunk of *source*, unmodified.

    Raises:
        StreamTruncatedError: when a truncated/unterminated stream is detected
            (only when the guard is enabled).
    """
    if not guard_enabled():
        yield from source
        return

    tid = trace_id or str(uuid.uuid4())
    state = _StreamState(trace_id=tid)

    iterator = iter(source)
    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        except Exception as exc:  # condition (c): dropped mid-chunk
            state.connection_dropped = True
            state.drop_detail = type(exc).__name__
            break
        if not isinstance(chunk, (bytes, bytearray)):
            chunk = bytes(str(chunk), "utf-8")
        state.note_chunk(bytes(chunk))
        yield bytes(chunk)

    reason = _detect_truncation(state)
    if reason is None:
        return

    _emit_provider_error(state, reason, db_path=db_path)
    raise StreamTruncatedError(
        partial_content=bytes(state.buffer),
        trace_id=state.trace_id,
        reason=reason,
        last_event_kind=state.last_event_kind,
        bytes_received=state.bytes_received,
        time_since_last_chunk_ms=state.time_since_last_chunk_ms(),
    )


def _detect_truncation(state: _StreamState) -> Optional[str]:
    """Return a short reason string if the stream was truncated, else None.

    Order of precedence: connection drop (most authoritative) → unterminated
    JSON envelope → missing message_stop on an SSE stream. An empty stream is
    treated as a connection-dropped truncation (nothing useful arrived).
    """
    if state.connection_dropped:
        return f"connection_dropped_mid_chunk:{state.drop_detail or 'unknown'}"
    if state.bytes_received == 0:
        return "empty_stream"
    if state.json_envelope_unterminated:
        return "json_envelope_unterminated"
    if _looks_like_sse(bytes(state.buffer)) and not state.saw_message_stop:
        return "message_stop_missing"
    return None


# ---------------------------------------------------------------------------
# Telemetry — provider.error event into monitor.db (wire plane only)
# ---------------------------------------------------------------------------


def _resolve_monitor_db(db_path: Optional[str]) -> Optional[str]:
    """Resolve the monitor.db write path via _paths (or an explicit override)."""
    if db_path:
        return db_path
    try:
        from tokenpak import _paths

        resolved = _paths.monitor_db(mode="write")
        return str(resolved) if resolved else None
    except Exception:
        return None


def _ensure_events_table(conn: sqlite3.Connection) -> None:
    """Create the additive provider_events table if absent.

    This is additive: it never alters the ``requests`` ledger schema and is
    safe to call repeatedly.
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_EVENTS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event TEXT NOT NULL,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            trace_id TEXT,
            bytes_received INTEGER DEFAULT 0,
            last_event_kind TEXT DEFAULT '',
            time_since_last_chunk_ms INTEGER DEFAULT 0,
            reason TEXT DEFAULT ''
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_provider_events_ts ON {_EVENTS_TABLE}(timestamp)")


def _emit_provider_error(
    state: _StreamState,
    reason: str,
    *,
    db_path: Optional[str] = None,
) -> bool:
    """Write a ``provider.error`` (kind=stream_truncated) row to monitor.db.

    Returns True on a successful write, False if no DB could be resolved or the
    write failed (telemetry is best-effort and never raises into the caller).

    Only structural attrs are recorded — NEVER raw provider body content.
    """
    resolved = _resolve_monitor_db(db_path)
    if not resolved:
        return False
    try:
        conn = sqlite3.connect(str(resolved), timeout=5)
        try:
            _ensure_events_table(conn)
            conn.execute(
                f"""
                INSERT INTO {_EVENTS_TABLE}
                    (timestamp, event, kind, severity, trace_id,
                     bytes_received, last_event_kind,
                     time_since_last_chunk_ms, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    EVENT_NAME,
                    EVENT_KIND,
                    EVENT_SEVERITY,
                    state.trace_id,
                    int(state.bytes_received),
                    state.last_event_kind,
                    int(state.time_since_last_chunk_ms()),
                    reason,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def read_provider_errors(
    db_path: Optional[str] = None,
    *,
    kind: str = EVENT_KIND,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Read recent provider.error rows from monitor.db (for doctor/inspection).

    Returns an empty list when the DB or table is absent.
    """
    resolved = _resolve_monitor_db(db_path)
    if not resolved:
        return []
    try:
        conn = sqlite3.connect(str(resolved), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (_EVENTS_TABLE,),
            )
            if cur.fetchone() is None:
                return []
            rows = conn.execute(
                f"SELECT * FROM {_EVENTS_TABLE} WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Self-test exercised by ``tokenpak doctor --stream``
# ---------------------------------------------------------------------------


def self_check(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Exercise the truncation path with a FAKE provider closing mid-chunk.

    Drives :func:`guarded_stream` over a fake SSE provider that emits a couple
    of events and then ends WITHOUT ``event: message_stop`` (i.e. truncated).
    Returns a result dict describing whether the guard flagged it.

    The check uses an isolated temp monitor.db so it never pollutes the user's
    real ledger, and is independent of the live ``TPK_STREAM_GUARD`` setting
    (it forces the guard on for the duration of the check).
    """
    import tempfile

    def _fake_truncating_provider() -> Iterator[bytes]:
        yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
        yield b'event: content_block_delta\ndata: {"type":"content_block_'
        # Connection drops here — no message_stop, mid-chunk.
        return

    prev = os.environ.get(GUARD_ENV)
    os.environ[GUARD_ENV] = "1"
    tmp = db_path
    cleanup = False
    if tmp is None:
        fd, tmp = tempfile.mkstemp(prefix="tpk-stream-selfcheck-", suffix=".db")
        os.close(fd)
        cleanup = True

    result: Dict[str, Any] = {
        "check": "stream_guard",
        "flagged": False,
        "code": None,
        "trace_id": None,
        "event_written": False,
        "reason": None,
    }
    try:
        try:
            for _ in guarded_stream(_fake_truncating_provider(), db_path=tmp):
                pass
        except StreamTruncatedError as err:
            result["flagged"] = True
            result["code"] = err.code
            result["trace_id"] = err.trace_id
            result["reason"] = err.reason
            rows = read_provider_errors(db_path=tmp)
            result["event_written"] = any(r.get("trace_id") == err.trace_id for r in rows)
    finally:
        if prev is None:
            os.environ.pop(GUARD_ENV, None)
        else:
            os.environ[GUARD_ENV] = prev
        if cleanup:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(tmp + suffix)
                except OSError:
                    pass

    result["passed"] = bool(
        result["flagged"] and result["code"] == STREAM_TRUNCATED_CODE and result["event_written"]
    )
    return result


__all__ = [
    "GUARD_ENV",
    "STREAM_TRUNCATED_CODE",
    "STREAM_TRUNCATED_REMEDY",
    "EVENT_NAME",
    "EVENT_KIND",
    "EVENT_SEVERITY",
    "guard_enabled",
    "guarded_stream",
    "StreamTruncatedError",
    "read_provider_errors",
    "self_check",
]
