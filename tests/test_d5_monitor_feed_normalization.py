# SPDX-License-Identifier: Apache-2.0
"""D5 — monitor.db feed normalization regression tests.

Covers the two D5 deliverables (packet p0-d5-monitor-db-feed-normalization-2026-05-31):

  Part 1 — resolver unification: ``status``, ``_cli_core``, ``doctor``, and the
           proxy writer all resolve the SAME monitor.db through the single
           canonical resolver ``tokenpak._paths.monitor_db()`` (no split-brain).
  Part 2 — finish Fix A attribution: ``agent_id`` / ``cycle_id`` are part of the
           schema and persisted by ``Monitor.log()`` when the request provides
           the ``X-Tokenpak-Agent`` / ``X-Tokenpak-Cycle`` headers; genuinely
           unknown rows carry the ``''`` sentinel (Std 34 §1.1), never NULL,
           never fabricated.
"""
import os
import sqlite3

import pytest

from tokenpak import _paths
from tokenpak.proxy import monitor as _monitor_mod
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.request_pipeline import _resolve_agent_id, _resolve_cycle_id


def _drain(db_path):
    """Block until the async write queue has flushed, then return all rows."""
    try:
        _monitor_mod._DB_WRITE_QUEUE.join()
    except Exception:
        pass
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT session_id, agent_id, cycle_id FROM requests ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Part 2 — schema + attribution persistence
# --------------------------------------------------------------------------

def test_schema_has_attribution_columns(tmp_path):
    db = tmp_path / "monitor.db"
    Monitor(str(db))
    cols = [r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(requests)")]
    assert "session_id" in cols
    assert "agent_id" in cols
    assert "cycle_id" in cols


def test_log_persists_agent_and_cycle(tmp_path):
    db = tmp_path / "monitor.db"
    m = Monitor(str(db))
    m.log(
        model="claude-opus-4-8", input_tokens=100, output_tokens=20, cost=0.01,
        latency_ms=50, status_code=200, endpoint="http://x",
        session_id="s-abc", agent_id="agent-alpha", cycle_id="cycle-42",
    )
    rows = _drain(db)
    assert rows, "row was not persisted"
    assert rows[-1] == ("s-abc", "agent-alpha", "cycle-42")


def test_log_uses_empty_sentinel_not_null_when_absent(tmp_path):
    db = tmp_path / "monitor.db"
    m = Monitor(str(db))
    m.log(
        model="claude-haiku-4-5", input_tokens=5, output_tokens=1, cost=0.0,
        latency_ms=10, status_code=200, endpoint="http://y",
    )
    rows = _drain(db)
    assert rows, "row was not persisted"
    # Std 34 §1.1: '' sentinel, never NULL — so the tuple is exactly ('', '', '')
    assert rows[-1] == ("", "", "")


# --------------------------------------------------------------------------
# Part 2 — header resolvers
# --------------------------------------------------------------------------

def test_resolve_agent_id_lowercased_case_insensitive():
    assert _resolve_agent_id({"X-Tokenpak-Agent": "Agent-Alpha"}) == "agent-alpha"
    assert _resolve_agent_id({"x-tokenpak-agent": "WORKER2"}) == "worker2"


def test_resolve_agent_id_sentinel_when_absent():
    assert _resolve_agent_id({}) == ""
    assert _resolve_agent_id({"X-Other": "v"}) == ""


def test_resolve_cycle_id_captured_or_sentinel():
    # captured verbatim (not lower-cased — cycle ids may be case-significant)
    assert _resolve_cycle_id({"X-Tokenpak-Cycle": "C-9"}) == "C-9"
    # no caller sets it today -> sentinel, never fabricated
    assert _resolve_cycle_id({}) == ""


def test_resolvers_handle_httpmessage():
    from email.message import Message
    msg = Message()
    msg["x-tokenpak-agent"] = "Beta"
    assert _resolve_agent_id(msg) == "beta"
    assert _resolve_cycle_id(msg) == ""


# --------------------------------------------------------------------------
# Part 1 — resolver unification / path parity (the core acceptance criterion)
# --------------------------------------------------------------------------

def test_path_parity_all_readers_and_writer_agree(tmp_path, monkeypatch):
    """status, _cli_core, doctor's resolution, and the writer resolve ONE DB."""
    db = tmp_path / "monitor.db"
    Monitor(str(db))  # create a valid DB (has the requests table)
    # Pin the canonical resolver to this DB via the env override (first
    # candidate in the chain), so the test is deterministic regardless of the
    # developer's real ~/.tpk / ~/.tokenpak / ~/tokenpak state.
    monkeypatch.setenv("TOKENPAK_DB", str(db))

    from tokenpak._cli_core import _get_monitor_db_path
    from tokenpak.cli.commands.status import _get_db_path

    target = os.path.realpath(str(db))
    canon_read = _paths.monitor_db(mode="read")
    canon_write = _paths.monitor_db(mode="write")

    assert os.path.realpath(str(canon_read)) == target
    assert os.path.realpath(str(canon_write)) == target
    assert os.path.realpath(_get_db_path()) == target
    assert os.path.realpath(str(_get_monitor_db_path())) == target


def test_readers_delegate_to_canonical_resolver(tmp_path, monkeypatch):
    """status + _cli_core must return exactly what _paths.monitor_db() returns."""
    db = tmp_path / "monitor.db"
    Monitor(str(db))
    monkeypatch.setenv("TOKENPAK_DB", str(db))

    from tokenpak._cli_core import _get_monitor_db_path
    from tokenpak.cli.commands.status import _get_db_path

    canon = os.path.realpath(str(_paths.monitor_db(mode="read")))
    assert os.path.realpath(_get_db_path()) == canon
    assert os.path.realpath(str(_get_monitor_db_path())) == canon
