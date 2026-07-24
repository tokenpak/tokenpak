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
        model="claude-opus-4-8",
        input_tokens=100,
        output_tokens=20,
        cost=0.01,
        latency_ms=50,
        status_code=200,
        endpoint="http://x",
        session_id="s-abc",
        agent_id="agent-alpha",
        cycle_id="cycle-42",
    )
    rows = _drain(db)
    assert rows, "row was not persisted"
    assert rows[-1] == ("s-abc", "agent-alpha", "cycle-42")


def test_log_uses_empty_sentinel_not_null_when_absent(tmp_path):
    db = tmp_path / "monitor.db"
    m = Monitor(str(db))
    m.log(
        model="claude-haiku-4-5",
        input_tokens=5,
        output_tokens=1,
        cost=0.0,
        latency_ms=10,
        status_code=200,
        endpoint="http://y",
    )
    rows = _drain(db)
    assert rows, "row was not persisted"
    # Std 34 §1.1: '' sentinel, never NULL — so the tuple is exactly ('', '', '')
    assert rows[-1] == ("", "", "")


def test_compat_monitor_preserves_legacy_positional_tail(tmp_path):
    from tokenpak.core.runtime.proxy import Monitor as CompatibilityMonitor

    db = tmp_path / "compat-monitor.db"
    monitor = CompatibilityMonitor(str(db))
    monitor.log(
        "model",
        1,
        2,
        0.0,
        3,
        200,
        "/v1/messages",
        "hybrid",
        4,
        5,
        6,
        "source",
        7,
        8,
        9,
        "session-id",
        "stable-hash",
        "volatile-hash",
    )

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT session_id, stable_hash, volatile_hash FROM requests").fetchone()
    finally:
        conn.close()
    assert row == ("session-id", "stable-hash", "volatile-hash")


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


# --------------------------------------------------------------------------
# AT-C — honest platform-origin attribution (attribution_source)
# --------------------------------------------------------------------------


def _attr_rows(db_path):
    try:
        _monitor_mod._DB_WRITE_QUEUE.join()
    except Exception:
        pass
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT attribution_source FROM requests ORDER BY id").fetchall()
    finally:
        conn.close()


def test_schema_has_attribution_source_column(tmp_path):
    db = tmp_path / "monitor.db"
    Monitor(str(db))
    cols = [r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(requests)")]
    assert "attribution_source" in cols


def test_log_persists_known_attribution_source(tmp_path):
    db = tmp_path / "monitor.db"
    m = Monitor(str(db))
    m.log(
        model="claude-opus-4-8",
        input_tokens=10,
        output_tokens=2,
        cost=0.0,
        latency_ms=5,
        status_code=200,
        endpoint="http://x",
        attribution_source="openclaw_active_session_file",
    )
    rows = _attr_rows(db)
    assert rows and rows[-1][0] == "openclaw_active_session_file"


def test_attribution_source_empty_sentinel_when_unknown(tmp_path):
    db = tmp_path / "monitor.db"
    m = Monitor(str(db))
    m.log(
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        latency_ms=1,
        status_code=200,
        endpoint="http://y",
    )
    rows = _attr_rows(db)
    # '' sentinel (never NULL, never fabricated) so 'non-empty == known origin'
    assert rows and rows[-1][0] == ""


def test_path_c_mapping_unknown_origin_is_empty_not_fabricated():
    """Wiring rule: extractor None -> '' (honest unknown); openclaw -> a
    non-empty, evidence-graded source, NEVER the proxy's own name."""
    from tokenpak.services.routing_service.platform_bridge import _openclaw_extract

    origin = _openclaw_extract({"User-Agent": "claude-code/2.1.0"}, b"")
    mapped = (origin.attribution_source if origin is not None else "") or ""
    assert mapped == ""

    oc = _openclaw_extract({"User-Agent": "openclaw/2026.4.28-1"}, b"")
    assert oc is not None
    mapped_oc = (oc.attribution_source if oc is not None else "") or ""
    assert mapped_oc != ""
    assert "tokenpak" not in mapped_oc.lower()


# --------------------------------------------------------------------------
# AT-D — attribution coverage metric (% known origin)
# --------------------------------------------------------------------------


def test_attribution_coverage_metric(tmp_path):
    """Coverage = % of requests rows with a non-empty (known) origin."""
    from tokenpak.cli.commands.doctor import attribution_coverage

    db = tmp_path / "monitor.db"
    m = Monitor(str(db))
    for src in (
        "openclaw_active_session_file",
        "anonymous_user_agent_only",
        "openclaw_active_session_file",
    ):
        m.log(
            model="claude-opus-4-8",
            input_tokens=1,
            output_tokens=1,
            cost=0.0,
            latency_ms=1,
            status_code=200,
            endpoint="http://x",
            attribution_source=src,
        )
    m.log(
        model="claude-opus-4-8",
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        latency_ms=1,
        status_code=200,
        endpoint="http://y",
    )  # unknown -> ''
    _drain(db)
    known, total, pct = attribution_coverage(str(db))
    assert (known, total) == (3, 4)
    assert pct == pytest.approx(75.0)


def test_attribution_coverage_graceful_when_absent(tmp_path):
    from tokenpak.cli.commands.doctor import attribution_coverage

    assert attribution_coverage(str(tmp_path / "missing.db")) == (0, 0, None)
