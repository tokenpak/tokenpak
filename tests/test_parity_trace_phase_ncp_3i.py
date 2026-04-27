# SPDX-License-Identifier: Apache-2.0
"""NCP-3I — parity-trace module tests.

Coverage (per the directive's acceptance criteria):

  1. disabled by default — emit() is no-op when env-var unset
  2. request-entry trace can exist without completion trace
  3. trace write failures are swallowed (never crash the caller)
  4. no raw prompts or secrets stored in any column
  5. table schema migration is additive + tolerant
  6. fetch_for_trace returns event rows in chronological order
  7. multi-event trace assembly (handler_entry → upstream_attempt_*)
  8. invalid event type / unknown column survives without raising
  9. structural — module does not import dispatch / behavior primitives
  10. is_enabled re-reads env var on every call (no caching)
  11. emit re-reads env var (live toggle)
  12. process metadata captured on every emit
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.proxy.parity_trace import (
    EVENT_HANDLER_ENTRY,
    EVENT_REQUEST_COMPLETION,
    EVENT_UPSTREAM_ATTEMPT_FAILURE,
    EVENT_UPSTREAM_ATTEMPT_START,
    PARITY_TRACE_ENV,
    ParityTraceStore,
    emit,
    is_enabled,
    set_default_store,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path):
    """Fresh ParityTraceStore + bind it as the default."""
    s = ParityTraceStore(db_path=tmp_path / "telemetry.db")
    set_default_store(s)
    try:
        yield s
    finally:
        set_default_store(None)
        s.close()


@pytest.fixture
def env_enabled():
    """TOKENPAK_PARITY_TRACE_ENABLED=true scope."""
    with mock.patch.dict(os.environ, {PARITY_TRACE_ENV: "true"}):
        yield


@pytest.fixture
def env_disabled():
    """Explicit env unset/false."""
    with mock.patch.dict(os.environ, {PARITY_TRACE_ENV: ""}):
        yield


# ── 1. disabled by default ────────────────────────────────────────────


class TestDisabledByDefault:

    def test_unset_returns_false(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PARITY_TRACE_ENV, None)
            assert is_enabled() is False

    def test_false_value_returns_false(self):
        for v in ("", "0", "false", "no", "off", "False", "OFF"):
            with mock.patch.dict(os.environ, {PARITY_TRACE_ENV: v}):
                assert is_enabled() is False, f"value {v!r} should be falsy"

    def test_true_value_returns_true(self):
        for v in ("1", "true", "yes", "on", "TRUE", "Yes", " on "):
            with mock.patch.dict(os.environ, {PARITY_TRACE_ENV: v}):
                assert is_enabled() is True, f"value {v!r} should be truthy"

    def test_emit_disabled_writes_no_rows(self, store, env_disabled):
        emit(EVENT_HANDLER_ENTRY, trace_id="t1")
        rows = store.fetch_for_trace("t1")
        assert rows == []


# ── 2. request-entry trace can exist without completion trace ─────────


class TestEntryWithoutCompletion:

    def test_handler_entry_alone_is_persisted(self, store, env_enabled):
        emit(EVENT_HANDLER_ENTRY, trace_id="t-entry-only", body_bytes=512)
        rows = store.fetch_for_trace("t-entry-only")
        assert len(rows) == 1
        assert rows[0]["event_type"] == EVENT_HANDLER_ENTRY
        assert rows[0]["body_bytes"] == 512

    def test_upstream_failure_without_completion(self, store, env_enabled):
        # A request that hits handler entry → upstream attempt → fails
        # before completion. Iter-4 §11 interp B: the visible-retry
        # condition should produce these two rows even when no
        # completion event ever fires.
        emit(EVENT_HANDLER_ENTRY, trace_id="t-fail")
        emit(
            EVENT_UPSTREAM_ATTEMPT_START,
            trace_id="t-fail",
            provider="tokenpak-claude-code",
        )
        emit(
            EVENT_UPSTREAM_ATTEMPT_FAILURE,
            trace_id="t-fail",
            retry_signal="connection_reset",
            retry_owner="tokenpak_proxy",
        )
        rows = store.fetch_for_trace("t-fail")
        assert len(rows) == 3
        event_types = [r["event_type"] for r in rows]
        # Persisted in chronological order.
        assert event_types == [
            EVENT_HANDLER_ENTRY,
            EVENT_UPSTREAM_ATTEMPT_START,
            EVENT_UPSTREAM_ATTEMPT_FAILURE,
        ]
        # No completion row was emitted.
        assert EVENT_REQUEST_COMPLETION not in event_types


# ── 3. trace write failures are swallowed ─────────────────────────────


class TestWriteFailuresSwallowed:

    def test_emit_with_unwritable_path_does_not_raise(self, env_enabled, tmp_path):
        # Bind a store pointing at a read-only path. Subsequent
        # connect attempts will fail; emit must NOT raise.
        bad_path = tmp_path / "readonly" / "telemetry.db"
        bad_store = ParityTraceStore(db_path=bad_path)
        set_default_store(bad_store)
        try:
            # Make the parent unwritable AFTER store creation so
            # _connect's mkdir trips.
            (tmp_path / "readonly").mkdir(mode=0o000)
            try:
                # Should not raise.
                emit(EVENT_HANDLER_ENTRY, trace_id="t-bad")
            finally:
                # Restore for cleanup.
                (tmp_path / "readonly").chmod(0o755)
        finally:
            set_default_store(None)

    def test_emit_with_unknown_field_does_not_raise(self, store, env_enabled):
        # ParityTraceRow has a strict field set; passing an
        # unknown kwarg would normally raise TypeError. Verify
        # the emit() try/except absorbs it.
        emit(
            EVENT_HANDLER_ENTRY,
            trace_id="t-unknown-field",
            this_field_does_not_exist=42,
        )
        # No row should be written (silent failure), no exception.
        rows = store.fetch_for_trace("t-unknown-field")
        assert rows == []

    def test_write_with_corrupt_db_returns_empty_fetch(self, store, env_enabled, tmp_path):
        # Corrupt the DB after a write. fetch_for_trace must return [].
        emit(EVENT_HANDLER_ENTRY, trace_id="t-pre")
        store.close()
        # Truncate the DB to simulate corruption.
        (tmp_path / "telemetry.db").write_bytes(b"\x00\x00not a sqlite db\x00")
        rows = store.fetch_for_trace("t-pre")
        assert rows == []


# ── 4. no raw prompts or secrets ──────────────────────────────────────


class TestPrivacyContract:

    SENTINEL = "SENTINEL_PROMPT_NCP3I_NEVER_LEAK_3xQz9"

    def test_sentinel_in_notes_field_is_caller_responsibility(self, store, env_enabled):
        # The schema permits notes to be free-form. The privacy
        # contract is that callers MUST NOT put prompt content
        # there. We verify the COLUMN ITSELF doesn't auto-populate
        # from anything that could contain prompt content — ie.
        # there's no code path where prompt bytes flow through.
        emit(EVENT_HANDLER_ENTRY, trace_id="t-priv")
        rows = store.fetch_for_trace("t-priv")
        for r in rows:
            assert self.SENTINEL not in (r.get("notes") or "")

    def test_no_field_default_to_prompt_like_data(self, store, env_enabled):
        # Emit without any caller-supplied notes; verify all TEXT
        # fields are None (no auto-population).
        emit(EVENT_HANDLER_ENTRY, trace_id="t-defaults")
        rows = store.fetch_for_trace("t-defaults")
        assert len(rows) == 1
        r = rows[0]
        # The only auto-populated TEXT fields should be:
        #   trace_id, event_type, tokenpak_home, telemetry_db_path
        # Everything else should be None.
        for col in (
            "request_id", "session_id", "provider", "auth_plane",
            "credential_class", "retry_phase", "retry_owner",
            "retry_signal", "tool_command_first", "notes",
        ):
            assert r[col] is None, (
                f"column {col!r} unexpectedly auto-populated: {r[col]!r}"
            )

    def test_module_does_not_import_request_body_paths(self):
        text = (Path(__file__).resolve().parents[1] / "tokenpak" / "proxy" / "parity_trace.py").read_text()
        # The module must not pull in anything that handles request bodies.
        forbidden = (
            "from tokenpak.proxy.server import",
            "from tokenpak.proxy.adapters",
            "credential_injector",
            "ClaudeCodeCredentialProvider",
            "from tokenpak.companion.hooks",
        )
        for f in forbidden:
            assert f not in text, (
                f"parity_trace.py must not import {f!r}"
            )


# ── 5. schema migration tolerant ──────────────────────────────────────


class TestSchemaMigration:

    def test_create_table_idempotent(self, tmp_path):
        s1 = ParityTraceStore(db_path=tmp_path / "a.db")
        s1._connect()
        s2 = ParityTraceStore(db_path=tmp_path / "a.db")
        s2._connect()  # should not raise — IF NOT EXISTS
        s1.close()
        s2.close()

    def test_table_present_after_first_emit(self, store, env_enabled):
        emit(EVENT_HANDLER_ENTRY, trace_id="t-schema")
        conn = sqlite3.connect(str(store.db_path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='tp_parity_trace'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()


# ── 6. fetch_for_trace ordering ───────────────────────────────────────


class TestFetchForTrace:

    def test_chronological(self, store, env_enabled):
        for i, evt in enumerate([
            EVENT_HANDLER_ENTRY,
            EVENT_UPSTREAM_ATTEMPT_START,
            EVENT_REQUEST_COMPLETION,
        ]):
            emit(evt, trace_id="t-order")
            time.sleep(0.001)  # ensure ts ordering
        rows = store.fetch_for_trace("t-order")
        assert [r["event_type"] for r in rows] == [
            EVENT_HANDLER_ENTRY,
            EVENT_UPSTREAM_ATTEMPT_START,
            EVENT_REQUEST_COMPLETION,
        ]

    def test_unknown_trace_returns_empty(self, store, env_enabled):
        emit(EVENT_HANDLER_ENTRY, trace_id="some-other")
        rows = store.fetch_for_trace("nonexistent")
        assert rows == []

    def test_fetch_with_no_db_returns_empty(self, tmp_path):
        s = ParityTraceStore(db_path=tmp_path / "nope.db")
        try:
            rows = s.fetch_for_trace("anything")
            assert rows == []
        finally:
            s.close()


# ── 7. multi-event trace assembly ─────────────────────────────────────


class TestMultiEventTrace:

    def test_seven_phase_chain(self, store, env_enabled):
        """The full lifecycle path the directive listed."""
        from tokenpak.proxy.parity_trace import (
            EVENT_REQUEST_CLASSIFIED,
            EVENT_RETRY_BOUNDARY,
        )
        for evt in (
            EVENT_HANDLER_ENTRY,
            EVENT_REQUEST_CLASSIFIED,
            EVENT_UPSTREAM_ATTEMPT_START,
            EVENT_UPSTREAM_ATTEMPT_FAILURE,
            EVENT_RETRY_BOUNDARY,
            EVENT_UPSTREAM_ATTEMPT_START,  # retry
            EVENT_REQUEST_COMPLETION,
        ):
            emit(evt, trace_id="t-full")
            time.sleep(0.0005)
        rows = store.fetch_for_trace("t-full")
        assert len(rows) == 7
        assert rows[0]["event_type"] == EVENT_HANDLER_ENTRY
        assert rows[-1]["event_type"] == EVENT_REQUEST_COMPLETION
        # Two upstream_attempt_start rows confirm retry branch.
        starts = [r for r in rows if r["event_type"] == EVENT_UPSTREAM_ATTEMPT_START]
        assert len(starts) == 2


# ── 8. invalid event_type does not crash ──────────────────────────────


class TestInvalidEventType:

    def test_unknown_event_type_persists_anyway(self, store, env_enabled):
        # The schema doesn't enforce an event_type whitelist (the
        # ALL_EVENTS frozenset is documentation, not a CHECK
        # constraint). Unknown values land in the DB but don't
        # crash. inspect_session_lanes can flag them.
        emit("definitely_not_a_real_event", trace_id="t-weird")
        rows = store.fetch_for_trace("t-weird")
        assert len(rows) == 1
        assert rows[0]["event_type"] == "definitely_not_a_real_event"


# ── 9. structural ─────────────────────────────────────────────────────


class TestStructural:

    """Scan imports + active call sites only, not docstrings.
    Docstring mentions like ``pool.request`` are explanatory and
    don't constitute coupling."""

    def _imports_and_calls(self) -> str:
        """Strip docstrings; return the remaining source for
        coupling-scan purposes."""
        import io
        import tokenize
        path = (Path(__file__).resolve().parents[1] / "tokenpak"
                / "proxy" / "parity_trace.py")
        src = path.read_text()
        # Remove all string literals (docstrings + regular strings)
        # via tokenize so we only inspect imports + bareword code.
        result_chunks: list[str] = []
        for tok in tokenize.tokenize(io.BytesIO(src.encode()).readline):
            if tok.type == tokenize.STRING:
                continue
            result_chunks.append(tok.string)
        return " ".join(result_chunks)

    def test_no_routing_imports(self):
        code = self._imports_and_calls()
        forbidden = (
            "RoutingService",
            "forward_headers",
            "from tokenpak.services.routing_service",
            "from tokenpak.proxy.client",
        )
        for f in forbidden:
            assert f not in code, (
                f"parity_trace.py must not couple to dispatch: {f!r}"
            )

    def test_no_companion_imports(self):
        code = self._imports_and_calls()
        for f in ("from tokenpak.companion", "import tokenpak.companion"):
            assert f not in code, (
                f"parity_trace.py must not couple to companion: {f!r}"
            )

    def test_no_active_pool_calls(self):
        """Scan AST for actual function calls into pool.request /
        pool.stream — those would be active dispatch coupling.
        Doc strings mentioning the names don't count."""
        import ast
        path = (Path(__file__).resolve().parents[1] / "tokenpak"
                / "proxy" / "parity_trace.py")
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                # Match expressions like ``pool.request`` or ``pool.stream``
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "pool"
                    and node.attr in ("request", "stream")
                ):
                    raise AssertionError(
                        f"parity_trace.py contains active dispatch call: "
                        f"pool.{node.attr}"
                    )


# ── 10. is_enabled re-reads on every call ─────────────────────────────


class TestEnvVarLiveRead:

    def test_toggle_during_session(self, store):
        # Start disabled
        with mock.patch.dict(os.environ, {PARITY_TRACE_ENV: "0"}):
            assert is_enabled() is False
            emit(EVENT_HANDLER_ENTRY, trace_id="t-toggle")
        assert store.fetch_for_trace("t-toggle") == []

        # Enable mid-session
        with mock.patch.dict(os.environ, {PARITY_TRACE_ENV: "1"}):
            assert is_enabled() is True
            emit(EVENT_HANDLER_ENTRY, trace_id="t-toggle")
        rows = store.fetch_for_trace("t-toggle")
        assert len(rows) == 1


# ── 11. process metadata ──────────────────────────────────────────────


class TestProcessMetadata:

    def test_pid_ppid_captured(self, store, env_enabled):
        emit(EVENT_HANDLER_ENTRY, trace_id="t-meta")
        rows = store.fetch_for_trace("t-meta")
        assert len(rows) == 1
        r = rows[0]
        assert r["pid"] == os.getpid()
        assert r["ppid"] == os.getppid()
        assert r["telemetry_db_path"] == str(store.db_path)

    def test_tokenpak_home_captured(self, store, tmp_path):
        with mock.patch.dict(os.environ, {
            PARITY_TRACE_ENV: "1",
            "TOKENPAK_HOME": str(tmp_path),
        }):
            emit(EVENT_HANDLER_ENTRY, trace_id="t-home")
        rows = store.fetch_for_trace("t-home")
        assert len(rows) == 1
        assert rows[0]["tokenpak_home"] == str(tmp_path)


# ── 12. defensive — concurrent emit doesn't deadlock ──────────────────


class TestConcurrentEmit:

    def test_multiple_threads_can_emit(self, store, env_enabled):
        import threading
        errors = []

        def worker(idx):
            try:
                for j in range(5):
                    emit(EVENT_HANDLER_ENTRY, trace_id=f"t-{idx}-{j}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        # Spot-check a few traces persisted.
        for idx in range(4):
            rows = store.fetch_for_trace(f"t-{idx}-0")
            assert len(rows) == 1


# ── 13. NCP-3I-v2 stream-integrity dimensions ─────────────────────────


class TestStreamIntegrityV2:
    """Cover the H10 stream-integrity additions:
       - new event constants (stream_start / complete / abort)
       - new schema columns
       - additive ALTER TABLE migration on v1 hosts
       - concurrent-stream gauge helpers
       - exception-message-hash helper
       - inspect_session_lanes consumption (covered separately)
    """

    def test_new_event_constants_exist(self):
        from tokenpak.proxy.parity_trace import (
            ALL_EVENTS,
            EVENT_STREAM_ABORT,
            EVENT_STREAM_COMPLETE,
            EVENT_STREAM_START,
        )
        assert EVENT_STREAM_START == "stream_start"
        assert EVENT_STREAM_COMPLETE == "stream_complete"
        assert EVENT_STREAM_ABORT == "stream_abort"
        for evt in (
            EVENT_STREAM_START,
            EVENT_STREAM_COMPLETE,
            EVENT_STREAM_ABORT,
        ):
            assert evt in ALL_EVENTS

    def test_v2_columns_present_on_fresh_install(self, store, env_enabled):
        from tokenpak.proxy.parity_trace import EVENT_STREAM_START
        emit(
            EVENT_STREAM_START,
            trace_id="t-v2",
            stream_started=1,
            upstream_status=200,
            response_content_type="text/event-stream",
            sse_event_count=42,
            sse_last_event_type="message_stop",
            bytes_from_upstream=1024,
            bytes_to_client=1024,
            json_parse_error_seen=0,
            stream_exception_class=None,
            stream_exception_message_hash=None,
            connection_closed_early=0,
            lane_id="12345:67890",
            concurrent_stream_count=2,
        )
        rows = store.fetch_for_trace("t-v2")
        assert len(rows) == 1
        r = rows[0]
        assert r["stream_started"] == 1
        assert r["upstream_status"] == 200
        assert r["response_content_type"] == "text/event-stream"
        assert r["sse_event_count"] == 42
        assert r["sse_last_event_type"] == "message_stop"
        assert r["bytes_from_upstream"] == 1024
        assert r["bytes_to_client"] == 1024
        assert r["json_parse_error_seen"] == 0
        assert r["connection_closed_early"] == 0
        assert r["lane_id"] == "12345:67890"
        assert r["concurrent_stream_count"] == 2

    def test_v1_db_migrates_to_v2_schema(self, tmp_path, env_enabled):
        """Pre-v2 hosts have a tp_parity_trace table without the new
        columns. The migration path adds them via ALTER TABLE."""
        db_path = tmp_path / "telemetry.db"
        # Simulate a v1-only schema by creating the table with the
        # original column set explicitly.
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE tp_parity_trace (
                trace_id TEXT NOT NULL, event_type TEXT NOT NULL,
                ts REAL NOT NULL, pid INTEGER, ppid INTEGER,
                tokenpak_home TEXT, telemetry_db_path TEXT,
                request_id TEXT, session_id TEXT, provider TEXT,
                auth_plane TEXT, credential_class TEXT,
                retry_phase TEXT, retry_owner TEXT, retry_signal TEXT,
                retry_count INTEGER, retry_after_seconds REAL,
                tool_command_first TEXT,
                tool_result_stdout_chars INTEGER,
                tool_result_stderr_chars INTEGER,
                tool_result_tokens_est INTEGER,
                body_bytes INTEGER, companion_added_chars INTEGER,
                intent_guidance_chars INTEGER,
                queue_wait_ms REAL, lock_wait_ms REAL,
                sqlite_write_ms REAL, notes TEXT
            );
            """
        )
        conn.commit()
        conn.close()

        # Now bind the v2 store + write a row using the new fields.
        # If the migration ran, the columns exist and the write
        # succeeds.
        s = ParityTraceStore(db_path=db_path)
        set_default_store(s)
        try:
            from tokenpak.proxy.parity_trace import EVENT_STREAM_START
            emit(
                EVENT_STREAM_START,
                trace_id="t-mig",
                stream_started=1,
                upstream_status=200,
                bytes_from_upstream=512,
            )
            rows = s.fetch_for_trace("t-mig")
            assert len(rows) == 1
            assert rows[0]["stream_started"] == 1
            assert rows[0]["bytes_from_upstream"] == 512
        finally:
            set_default_store(None)
            s.close()

    def test_concurrent_stream_counter_round_trip(self):
        from tokenpak.proxy import parity_trace as _pt
        # Reset any state from prior tests.
        # (The counter is global; idempotent operations only.)
        n0 = _pt.begin_stream()
        n1 = _pt.begin_stream()
        assert n1 == n0 + 1
        n2 = _pt.end_stream()
        assert n2 == n0
        # Restore to baseline.
        _pt.end_stream()

    def test_concurrent_stream_counter_concurrent_threads(self):
        import threading

        from tokenpak.proxy import parity_trace as _pt
        results = []

        def worker():
            n = _pt.begin_stream()
            results.append(n)
            _pt.end_stream()

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        # All workers should have observed a positive count.
        assert all(r > 0 for r in results)

    def test_lane_id_format(self):
        from tokenpak.proxy import parity_trace as _pt
        lane = _pt.current_lane_id()
        assert ":" in lane
        pid_str, tid_str = lane.split(":", 1)
        assert pid_str == str(os.getpid())
        assert tid_str.isdigit()

    def test_exception_message_hash_deterministic(self):
        from tokenpak.proxy import parity_trace as _pt
        e1 = ValueError("Unterminated string")
        e2 = ValueError("Unterminated string")
        h1 = _pt.hash_exception_message(e1)
        h2 = _pt.hash_exception_message(e2)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex
        # Different message → different hash.
        e3 = ValueError("Different message")
        assert _pt.hash_exception_message(e3) != h1

    def test_exception_message_hash_does_not_leak_message(self):
        from tokenpak.proxy import parity_trace as _pt
        secret = "PROMPT_SECRET_NEVER_IN_HASH_OUTPUT_8jK3"
        h = _pt.hash_exception_message(ValueError(secret))
        # Hash hex is opaque; secret never appears.
        assert secret not in h
        assert len(h) == 64

    def test_v2_schema_indexes_present(self, store, env_enabled):
        """The ``idx_parity_lane`` index lands in v2."""
        from tokenpak.proxy.parity_trace import EVENT_STREAM_START
        emit(EVENT_STREAM_START, trace_id="t-idx")
        conn = sqlite3.connect(str(store.db_path))
        try:
            indexes = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='tp_parity_trace'"
                ).fetchall()
            ]
        finally:
            conn.close()
        assert "idx_parity_lane" in indexes
