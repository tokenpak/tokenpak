# SPDX-License-Identifier: Apache-2.0
"""Tests for the cross-tool session handoff capture in the proxy app API.

Covers:
  - ``_is_handoff`` detection (typed entry vs HANDOFF_MARKER content sniff)
  - ``_extract_field`` edge cases (case, whitespace, embedded colons, absent)
  - ``_record_handoff`` artifact layout (current.json pointer, events.jsonl
    append log, readable capsule) + deterministic handoff_id
  - journal POST handler: sessions-table registration on first write and
    handoff mirroring into the 200 response
  - legacy-data compat: a journal.db with pre-existing entries but no session
    row is registered non-destructively (additive, idempotent)
  - capsule reserved-alias resolution (``active``/``latest``/``current`` →
    newest real capsule by mtime, never a frozen alias file)
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import time

from tokenpak.proxy import app_endpoints as ae

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeHandler:
    """Minimal stand-in for the HTTP handler `_send_json` writes through."""

    def __init__(self):
        self.status = None
        self.headers_sent = {}
        self.wfile = io.BytesIO()
        self.headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers_sent[key] = value

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _post_journal(session_id: str, content: str, entry_type: str = "user") -> FakeHandler:
    h = FakeHandler()
    ae._handle_journal_post(h, session_id, {"content": content, "entry_type": entry_type})
    return h


# ---------------------------------------------------------------------------
# _is_handoff
# ---------------------------------------------------------------------------


def test_handoff_detected_by_entry_type():
    assert ae._is_handoff("handoff", "anything") is True


def test_handoff_entry_type_case_and_whitespace_insensitive():
    assert ae._is_handoff("  Handoff ", "x") is True
    assert ae._is_handoff("HANDOFF", "") is True


def test_handoff_detected_by_marker_in_content():
    assert ae._is_handoff("user", "notes\nHANDOFF_MARKER: phase-3\nmore") is True


def test_handoff_not_detected_for_plain_entry():
    assert ae._is_handoff("user", "just a normal journal note") is False


def test_handoff_empty_inputs_are_safe():
    assert ae._is_handoff("", "") is False
    assert ae._is_handoff(None, None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _extract_field
# ---------------------------------------------------------------------------


def test_handoff_extract_field_basic():
    content = "intro\nHANDOFF_MARKER: phase-3-ready\ntail"
    assert ae._extract_field(content, "HANDOFF_MARKER") == "phase-3-ready"


def test_handoff_extract_field_strips_whitespace():
    content = "   HANDOFF_MARKER:    spaced out   "
    assert ae._extract_field(content, "HANDOFF_MARKER") == "spaced out"


def test_handoff_extract_field_value_may_contain_colons():
    content = "SECRET_DECISION: use plan B: the colon-bearing one"
    assert ae._extract_field(content, "SECRET_DECISION") == "use plan B: the colon-bearing one"


def test_handoff_extract_field_missing_returns_empty():
    assert ae._extract_field("no fields here", "HANDOFF_MARKER") == ""


def test_handoff_extract_field_empty_content_returns_empty():
    assert ae._extract_field("", "HANDOFF_MARKER") == ""
    assert ae._extract_field(None, "HANDOFF_MARKER") == ""  # type: ignore[arg-type]


def test_handoff_extract_field_first_match_wins():
    content = "HANDOFF_MARKER: first\nHANDOFF_MARKER: second"
    assert ae._extract_field(content, "HANDOFF_MARKER") == "first"


# ---------------------------------------------------------------------------
# _record_handoff — artifact layout
# ---------------------------------------------------------------------------


def test_handoff_record_writes_pointer_log_and_capsule(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_TOOL", "claude-code")
    content = "HANDOFF_MARKER: m-1\nSECRET_DECISION: keep going\nbody text"
    record = ae._record_handoff("sess-a", content)

    current = tmp_path / "run" / "handoff" / "current.json"
    events = tmp_path / "run" / "handoff" / "events.jsonl"
    capsule = tmp_path / "capsules" / "sess-a.md"
    assert current.exists() and events.exists() and capsule.exists()
    # no tmp residue from the atomic write
    assert not (tmp_path / "run" / "handoff" / "current.json.tmp").exists()

    loaded = json.loads(current.read_text(encoding="utf-8"))
    assert loaded == record
    assert loaded["session_id"] == "sess-a"
    assert loaded["marker"] == "m-1"
    assert loaded["secret_decision"] == "keep going"
    assert loaded["source_tool"] == "claude-code"
    assert loaded["payload_ref"] == "journal:sess-a"

    cap_text = capsule.read_text(encoding="utf-8")
    assert "m-1" in cap_text and "keep going" in cap_text


def test_handoff_events_log_appends(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    ae._record_handoff("sess-a", "HANDOFF_MARKER: one")
    ae._record_handoff("sess-a", "HANDOFF_MARKER: two")
    lines = (tmp_path / "run" / "handoff" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["marker"] == "one"
    assert json.loads(lines[1])["marker"] == "two"
    # current.json points at the latest
    latest = json.loads((tmp_path / "run" / "handoff" / "current.json").read_text())
    assert latest["marker"] == "two"


def test_handoff_id_deterministic_from_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    r1 = ae._record_handoff("sess-a", "HANDOFF_MARKER: stable\nnoise 1")
    r2 = ae._record_handoff("sess-b", "HANDOFF_MARKER: stable\nnoise 2")
    assert r1["handoff_id"] == r2["handoff_id"]  # basis is the marker
    r3 = ae._record_handoff("sess-c", "no marker at all")
    assert r3["handoff_id"] != r1["handoff_id"]  # falls back to content basis


# ---------------------------------------------------------------------------
# Journal POST handler — sessions-table registration + handoff mirroring
# ---------------------------------------------------------------------------


def test_handoff_journal_post_registers_session(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    h = _post_journal("sess-reg", "first entry")
    assert h.status == 200

    con = sqlite3.connect(str(tmp_path / "journal.db"))
    rows = list(con.execute("SELECT session_id, project_dir FROM sessions"))
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == "sess-reg"
    assert rows[0][1] == os.getcwd()


def test_handoff_journal_post_registration_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    _post_journal("sess-reg", "entry one")
    _post_journal("sess-reg", "entry two")

    con = sqlite3.connect(str(tmp_path / "journal.db"))
    n_sessions = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    n_entries = con.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    con.close()
    assert n_sessions == 1
    assert n_entries == 2


def test_handoff_journal_post_legacy_db_compat(tmp_path, monkeypatch):
    """Legacy-data guard: a journal.db with pre-existing entries but NO session
    row (data written before session registration existed) is migrated
    additively — the session row appears on the next write and prior entries
    survive untouched."""
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    from tokenpak.companion.journal.store import JournalStore

    store = JournalStore(db_path=tmp_path / "journal.db")
    store.add_entry(session_id="sess-legacy", entry_type="user", content="old entry")
    con = sqlite3.connect(str(tmp_path / "journal.db"))
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    con.close()

    h = _post_journal("sess-legacy", "new entry after upgrade")
    assert h.status == 200

    con = sqlite3.connect(str(tmp_path / "journal.db"))
    sessions = list(con.execute("SELECT session_id FROM sessions"))
    entries = [
        r[0]
        for r in con.execute(
            "SELECT content FROM entries WHERE session_id='sess-legacy' ORDER BY id"
        )
    ]
    con.close()
    assert sessions == [("sess-legacy",)]
    assert entries == ["old entry", "new entry after upgrade"]


def test_handoff_journal_post_mirrors_handoff_in_response(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    h = _post_journal("sess-h", "HANDOFF_MARKER: ready\nSECRET_DECISION: ship it")
    assert h.status == 200
    body = h.body()
    assert body["status"] == "ok"
    assert body["handoff"]["marker"] == "ready"
    assert body["handoff"]["secret_decision"] == "ship it"
    assert (tmp_path / "run" / "handoff" / "current.json").exists()


def test_handoff_journal_post_plain_entry_has_no_handoff_key(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    h = _post_journal("sess-p", "ordinary note")
    assert h.status == 200
    assert "handoff" not in h.body()
    assert not (tmp_path / "run" / "handoff").exists()


def test_handoff_journal_post_typed_handoff_entry_mirrors(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    h = _post_journal("sess-t", "no marker line here", entry_type="handoff")
    assert h.status == 200
    body = h.body()
    assert body["handoff"]["marker"] == ""
    assert body["handoff"]["summary"] == "no marker line here"


# ---------------------------------------------------------------------------
# Capsule reserved-alias resolution
# ---------------------------------------------------------------------------


def _get_capsule(session_id: str) -> FakeHandler:
    h = FakeHandler()
    ae._handle_capsule_get(h, session_id, {})
    return h


def test_handoff_capsule_alias_resolves_newest_real_capsule(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    capdir = tmp_path / "capsules"
    capdir.mkdir(parents=True)
    older = capdir / "sess-old.md"
    newer = capdir / "sess-new.md"
    stale_alias = capdir / "active.md"
    older.write_text("# old capsule\n")
    stale_alias.write_text("# stale alias body\n")
    newer.write_text("# new capsule\n")
    past = time.time() - 3600
    os.utime(older, (past, past))
    os.utime(stale_alias, (past - 60, past - 60))

    for alias in ("active", "latest", "current", "ACTIVE", " active "):
        h = _get_capsule(alias)
        assert h.status == 200, alias
        assert h.body()["session_id"] == "sess-new", alias


def test_handoff_capsule_alias_never_resolves_alias_files(tmp_path, monkeypatch):
    """Even if an alias file is the newest .md on disk, it is skipped."""
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    capdir = tmp_path / "capsules"
    capdir.mkdir(parents=True)
    real = capdir / "sess-real.md"
    real.write_text("# real\n")
    past = time.time() - 3600
    os.utime(real, (past, past))
    (capdir / "active.md").write_text("# alias newest\n")

    h = _get_capsule("active")
    assert h.status == 200
    assert h.body()["session_id"] == "sess-real"


def test_handoff_capsule_alias_404_when_no_real_capsules(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    capdir = tmp_path / "capsules"
    capdir.mkdir(parents=True)
    (capdir / "active.md").write_text("# only the alias exists\n")
    h = _get_capsule("active")
    assert h.status == 404


def test_handoff_capsule_exact_id_lookup_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    capdir = tmp_path / "capsules"
    capdir.mkdir(parents=True)
    (capdir / "sess-abc123.md").write_text("# target\n")
    h = _get_capsule("abc123")
    assert h.status == 200
    assert h.body()["session_id"] == "sess-abc123"
