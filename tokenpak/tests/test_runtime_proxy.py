# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.runtime.proxy — shim functions and Monitor subclass."""

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import pytest
from unittest.mock import patch, MagicMock

from tokenpak.runtime.proxy import (
    _resolve_session_id,
    _prune_mutation_audit,
    _write_mutation_audit,
    can_compress,
    Monitor,
    SESSION,
    _PROFILE_PRESETS,
)


# ---------------------------------------------------------------------------
# _resolve_session_id
# ---------------------------------------------------------------------------

class TestResolveSessionId:
    def _headers(self, data: dict):
        """Return a plain dict as headers (case-sensitive get)."""
        return data

    def test_claude_code_session_id_takes_priority(self):
        headers = {
            "X-Claude-Code-Session-Id": "cc-session-abc",
            "X-TokenPak-Session": "tp-session-xyz",
        }
        result = _resolve_session_id(headers, "claude-sonnet-4-6")
        assert result == "cc-session-abc"

    def test_tokenpak_session_used_when_no_cc_id(self):
        headers = {"X-TokenPak-Session": "tp-session-xyz"}
        result = _resolve_session_id(headers, "claude-sonnet-4-6")
        assert result == "tp-session-xyz"

    def test_falls_back_to_model_name(self):
        result = _resolve_session_id({}, "gpt-4o")
        assert result == "gpt-4o"

    def test_lowercase_header_accepted(self):
        headers = {"x-claude-code-session-id": "cc-lower"}
        result = _resolve_session_id(headers, "gpt-4o")
        assert result == "cc-lower"

    def test_title_case_header_accepted(self):
        headers = {"X-Claude-Code-Session-Id": "cc-title"}
        result = _resolve_session_id(headers, "gpt-4o")
        assert result == "cc-title"

    def test_empty_dict_headers_falls_back_to_model(self):
        result = _resolve_session_id({}, "gemini-pro")
        assert result == "gemini-pro"

    def test_object_without_get_returns_model(self):
        # Headers that have no .get() → behaves like no match
        class NoGetHeaders:
            pass
        result = _resolve_session_id(NoGetHeaders(), "some-model")
        assert result == "some-model"


# ---------------------------------------------------------------------------
# can_compress
# ---------------------------------------------------------------------------

class TestCanCompress:
    def test_transparent_mode_always_false(self):
        assert can_compress("low", "transparent") is False

    def test_strict_mode_always_false(self):
        assert can_compress("low", "strict") is False

    def test_safe_mode_always_false(self):
        assert can_compress("low", "safe") is False

    def test_other_mode_delegates_to_base(self):
        # The base _base_can_compress logic is called for other modes.
        # We only care that transparent/strict/safe short-circuit.
        # For a mode like "balanced" the result is delegated — just verify
        # it returns a bool without raising.
        result = can_compress("low", "balanced")
        assert isinstance(result, bool)

    def test_aggressive_mode_delegates(self):
        result = can_compress("low", "aggressive")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _prune_mutation_audit
# ---------------------------------------------------------------------------

class TestPruneMutationAudit:
    def _setup_db(self, db_path: str):
        """Create mutation_audit table and insert rows with varying timestamps."""
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE mutation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                session_id TEXT,
                timestamp TEXT NOT NULL,
                pre_hash TEXT,
                post_hash TEXT,
                rules_applied TEXT,
                cache_risk TEXT,
                rollback_possible INTEGER,
                mode TEXT
            )
        """)
        # Old row — 100 days ago
        conn.execute(
            "INSERT INTO mutation_audit (timestamp, mode) VALUES (datetime('now', '-100 days'), 'balanced')"
        )
        # Recent row — 1 day ago
        conn.execute(
            "INSERT INTO mutation_audit (timestamp, mode) VALUES (datetime('now', '-1 day'), 'balanced')"
        )
        conn.commit()
        return conn

    def test_prune_removes_old_rows(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = self._setup_db(db_path)
            deleted = _prune_mutation_audit(conn, ttl_days=30)
            conn.close()
            assert deleted == 1
            # Verify only one row remains
            conn2 = sqlite3.connect(db_path)
            count = conn2.execute("SELECT COUNT(*) FROM mutation_audit").fetchone()[0]
            conn2.close()
            assert count == 1
        finally:
            os.unlink(db_path)

    def test_prune_with_zero_ttl_removes_all(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = self._setup_db(db_path)
            # TTL of 0 days removes rows older than 'now', which is everything
            deleted = _prune_mutation_audit(conn, ttl_days=0)
            conn.close()
            assert deleted >= 1  # At least the 100-day-old row
        finally:
            os.unlink(db_path)

    def test_prune_returns_zero_when_nothing_to_prune(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = self._setup_db(db_path)
            # TTL of 200 days — nothing is that old
            deleted = _prune_mutation_audit(conn, ttl_days=200)
            conn.close()
            assert deleted == 0
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# _write_mutation_audit
# ---------------------------------------------------------------------------

class TestWriteMutationAudit:
    def _setup_db(self, db_path: str):
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE mutation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                session_id TEXT,
                timestamp TEXT NOT NULL,
                pre_hash TEXT,
                post_hash TEXT,
                rules_applied TEXT,
                cache_risk TEXT,
                rollback_possible INTEGER,
                mode TEXT
            )
        """)
        conn.commit()
        conn.close()

    def test_writes_row_with_correct_hashes(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._setup_db(db_path)
            body_pre = b'{"messages": [{"role": "user", "content": "hello"}]}'
            body_post = b'{"messages": [{"role": "user", "content": "hello modified"}]}'
            _write_mutation_audit(
                db_path=db_path,
                request_id=42,
                session_id="sess-001",
                body_pre=body_pre,
                body_post=body_post,
                rules_applied=["rule_a", "rule_b"],
                cache_risk="low",
                mode="balanced",
            )
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT * FROM mutation_audit").fetchone()
            conn.close()
            assert row is not None
            # Verify hashes
            assert row[4] == hashlib.sha256(body_pre).hexdigest()   # pre_hash
            assert row[5] == hashlib.sha256(body_post).hexdigest()  # post_hash
            # Verify rules_applied stored as JSON
            assert json.loads(row[6]) == ["rule_a", "rule_b"]
            # rollback_possible should be 1
            assert row[8] == 1
        finally:
            os.unlink(db_path)

    def test_identical_pre_post_hash(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            self._setup_db(db_path)
            body = b"same content"
            _write_mutation_audit(db_path, 1, "s", body, body, [], "none", "balanced")
            conn = sqlite3.connect(db_path)
            row = conn.execute("SELECT pre_hash, post_hash FROM mutation_audit").fetchone()
            conn.close()
            assert row[0] == row[1]
        finally:
            os.unlink(db_path)

    def test_write_fails_silently_on_bad_db(self):
        # Should not raise even if db_path is invalid
        _write_mutation_audit(
            db_path="/nonexistent/path/to.db",
            request_id=1,
            session_id="s",
            body_pre=b"x",
            body_post=b"y",
            rules_applied=[],
            cache_risk="low",
            mode="balanced",
        )


# ---------------------------------------------------------------------------
# Monitor subclass
# ---------------------------------------------------------------------------

class TestMonitor:
    def test_init_creates_db_with_extended_schema(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            m = Monitor(db_path=db_path)
            conn = sqlite3.connect(db_path)
            # Verify extended columns exist
            cols_info = conn.execute("PRAGMA table_info(requests)").fetchall()
            col_names = {c[1] for c in cols_info}
            assert "session_id" in col_names
            assert "stable_hash" in col_names
            assert "volatile_hash" in col_names
            # Verify mutation_audit table created
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            assert "mutation_audit" in tables
            assert "cache_invalidator_events" in tables
            conn.close()
        finally:
            os.unlink(db_path)

    def test_log_inserts_row_with_session_id(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            m = Monitor(db_path=db_path)
            m.log(
                model="claude-sonnet-4-6",
                input_tokens=100,
                output_tokens=20,
                cost=0.001,
                latency_ms=500,
                status_code=200,
                endpoint="/v1/messages",
                session_id="test-session-123",
                stable_hash="abc",
                volatile_hash="def",
            )
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT model, session_id, stable_hash, volatile_hash FROM requests"
            ).fetchone()
            conn.close()
            assert row[0] == "claude-sonnet-4-6"
            assert row[1] == "test-session-123"
            assert row[2] == "abc"
            assert row[3] == "def"
        finally:
            os.unlink(db_path)

    def test_log_does_not_raise_on_error(self):
        # Even with a bad db_path, log() should fail silently
        m = Monitor.__new__(Monitor)
        m.db_path = "/nonexistent/path/monitor.db"
        m.log(
            model="test",
            input_tokens=1,
            output_tokens=1,
            cost=0.0,
            latency_ms=10,
            status_code=200,
            endpoint="/v1/messages",
        )

    def test_second_init_is_idempotent(self):
        """Calling _init_db twice (e.g. on a pre-existing DB) should not fail."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            m1 = Monitor(db_path=db_path)
            m2 = Monitor(db_path=db_path)  # Should not raise
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# SESSION dict structure
# ---------------------------------------------------------------------------

class TestSessionDict:
    def test_required_keys_present(self):
        required = [
            "requests", "input_tokens", "output_tokens", "cost",
            "errors", "cache_read_tokens", "cache_creation_tokens",
            "cache_hits", "cache_misses", "start_time",
        ]
        for key in required:
            assert key in SESSION, f"Missing key: {key}"

    def test_numeric_defaults_are_zero_or_float(self):
        assert SESSION["requests"] == 0
        assert SESSION["errors"] == 0
        assert SESSION["cache_hits"] == 0
        assert SESSION["cache_misses"] == 0
        assert isinstance(SESSION["cost"], float)
        assert isinstance(SESSION["start_time"], float)


# ---------------------------------------------------------------------------
# _PROFILE_PRESETS — claude-code / transparent profiles
# ---------------------------------------------------------------------------

class TestProfilePresets:
    def test_claude_code_profile_present(self):
        assert "claude-code" in _PROFILE_PRESETS

    def test_transparent_profile_present(self):
        assert "transparent" in _PROFILE_PRESETS

    def test_claude_code_mode_is_transparent(self):
        assert _PROFILE_PRESETS["claude-code"]["TOKENPAK_MODE"] == "transparent"

    def test_transparent_mode_is_transparent(self):
        assert _PROFILE_PRESETS["transparent"]["TOKENPAK_MODE"] == "transparent"

    def test_claude_code_disables_compression(self):
        # Compact threshold should be very high to disable compression
        threshold = int(_PROFILE_PRESETS["claude-code"]["TOKENPAK_COMPACT_THRESHOLD_TOKENS"])
        assert threshold >= 99_999_999

    def test_claude_code_enables_trace(self):
        assert _PROFILE_PRESETS["claude-code"]["TOKENPAK_TRACE"] == "true"
