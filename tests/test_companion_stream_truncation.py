# SPDX-License-Identifier: Apache-2.0
"""Tests for the companion defensive truncated-stream guard.

Covers:
  * unit — fake provider closes mid-chunk -> StreamTruncatedError with the
    stable code, partial content, and a trace_id.
  * unit — fake provider emits ``event: message_stop`` then EOF -> NO false
    positive (clean passthrough, no error).
  * unit — feature flag off (TPK_STREAM_GUARD=0) -> pure passthrough.
  * integration — a monitor.db row is written for the truncation event and a
    sibling journal.db is left untouched (plane discipline).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from tokenpak.companion import stream as stream_mod
from tokenpak.companion.stream import (
    STREAM_TRUNCATED_CODE,
    StreamTruncatedError,
    guarded_stream,
    read_provider_errors,
)

# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


def _provider_closes_mid_chunk() -> Iterator[bytes]:
    """SSE-style provider that drops before sending ``message_stop``."""
    yield b'event: message_start\ndata: {"type":"message_start","message":{}}\n\n'
    yield b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n'
    yield b'event: content_block_delta\ndata: {"type":"content_block_'
    # Stream ends here — truncated mid-chunk, no message_stop.
    return


def _provider_clean_complete() -> Iterator[bytes]:
    """SSE-style provider that ends correctly with ``message_stop``."""
    yield b'event: message_start\ndata: {"type":"message_start","message":{}}\n\n'
    yield b'event: content_block_delta\ndata: {"type":"text","text":"hi"}\n\n'
    yield b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n'
    yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'


def _provider_raises_mid_chunk() -> Iterator[bytes]:
    """Provider whose byte iterator raises mid-stream (connection drop)."""
    yield b'event: message_start\ndata: {"type":"message_start"}\n\n'
    raise ConnectionError("peer reset")


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch):
    """Default the guard ON for each test unless a test overrides it."""
    monkeypatch.setenv("TPK_STREAM_GUARD", "1")


@pytest.fixture()
def monitor_db(tmp_path, monkeypatch) -> Path:
    """Point the monitor DB resolver at an isolated temp file via TOKENPAK_DB."""
    db = tmp_path / "monitor.db"
    # Seed a minimal valid monitor.db so _paths recognises it (read path needs
    # the `requests` table + >100 bytes), and so write-path resolution returns
    # this file rather than the user's real ledger.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, padding TEXT)"
    )
    conn.execute(
        "INSERT INTO requests (timestamp, model, padding) VALUES (?, ?, ?)",
        ("seed", "seed", "x" * 200),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    return db


# ---------------------------------------------------------------------------
# Unit — truncation detected
# ---------------------------------------------------------------------------


def test_mid_chunk_close_raises_truncated_with_partial_and_trace(monitor_db):
    received = []
    with pytest.raises(StreamTruncatedError) as excinfo:
        for chunk in guarded_stream(_provider_closes_mid_chunk(), db_path=str(monitor_db)):
            received.append(chunk)

    err = excinfo.value
    # Stable code surfaced to caller.
    assert err.code == STREAM_TRUNCATED_CODE
    # Partial content preserved (bytes seen before the cut).
    assert err.partial_content == b"".join(received)
    assert b"content_block_" in err.partial_content
    # Trace id present + stable across the error envelope.
    assert err.trace_id
    envelope = err.to_dict()["error"]
    assert envelope["code"] == STREAM_TRUNCATED_CODE
    assert envelope["trace_id"] == err.trace_id
    assert "max_output_tokens" in envelope["remedy"]
    assert envelope["partial_content"]  # partial text shown to the user
    # Reason classifies it as missing message_stop (SSE stream, no terminal).
    assert err.reason in {"message_stop_missing", "json_envelope_unterminated"}


def test_connection_drop_mid_chunk_is_flagged(monitor_db):
    with pytest.raises(StreamTruncatedError) as excinfo:
        for _ in guarded_stream(_provider_raises_mid_chunk(), db_path=str(monitor_db)):
            pass
    assert excinfo.value.code == STREAM_TRUNCATED_CODE
    assert "connection_dropped_mid_chunk" in excinfo.value.reason


# ---------------------------------------------------------------------------
# Unit — no false positive on a clean stream
# ---------------------------------------------------------------------------


def test_clean_message_stop_no_false_positive(monitor_db):
    received = []
    # Must NOT raise.
    for chunk in guarded_stream(_provider_clean_complete(), db_path=str(monitor_db)):
        received.append(chunk)
    assert b"message_stop" in b"".join(received)
    # No provider.error event written for a clean stream.
    assert read_provider_errors(db_path=str(monitor_db)) == []


# ---------------------------------------------------------------------------
# Unit — feature flag off => pure passthrough
# ---------------------------------------------------------------------------


def test_guard_disabled_passes_through(monitor_db, monkeypatch):
    monkeypatch.setenv("TPK_STREAM_GUARD", "0")
    received = []
    # Even a truncated stream must NOT raise when the guard is off.
    for chunk in guarded_stream(_provider_closes_mid_chunk(), db_path=str(monitor_db)):
        received.append(chunk)
    assert received  # bytes still delivered
    # No event written in passthrough mode.
    assert read_provider_errors(db_path=str(monitor_db)) == []


# ---------------------------------------------------------------------------
# Integration — monitor.db written, journal.db untouched (plane discipline)
# ---------------------------------------------------------------------------


def test_monitor_db_row_written_journal_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("TPK_STREAM_GUARD", "1")
    monitor = tmp_path / "monitor.db"
    journal = tmp_path / "journal.db"

    # Seed monitor.db so the resolver accepts it.
    conn = sqlite3.connect(str(monitor))
    conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, padding TEXT)")
    conn.execute("INSERT INTO requests (padding) VALUES (?)", ("x" * 200,))
    conn.commit()
    conn.close()
    monkeypatch.setenv("TOKENPAK_DB", str(monitor))

    assert not journal.exists()

    trace_id = None
    with pytest.raises(StreamTruncatedError) as excinfo:
        for _ in guarded_stream(_provider_closes_mid_chunk(), db_path=str(monitor)):
            pass
    trace_id = excinfo.value.trace_id

    # Wire-plane row landed in monitor.db.
    rows = read_provider_errors(db_path=str(monitor))
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "provider.error"
    assert row["kind"] == "stream_truncated"
    assert row["severity"] == "warn"
    assert row["trace_id"] == trace_id
    assert row["bytes_received"] > 0
    # Structural attrs only — no raw body column exists.
    assert "reason" in row
    assert all("content" not in k or k == "last_event_kind" for k in row.keys())

    # Plane discipline: journal.db was never created/written.
    assert not journal.exists()


def test_self_check_passes(monkeypatch):
    """The doctor self-check exercises the path and reports pass."""
    monkeypatch.delenv("TPK_STREAM_GUARD", raising=False)
    result = stream_mod.self_check()
    assert result["passed"] is True
    assert result["code"] == STREAM_TRUNCATED_CODE
    assert result["event_written"] is True
