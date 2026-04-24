# SPDX-License-Identifier: Apache-2.0
"""Session mapper — unit tests (v1.3.14, 2026-04-24).

The mapper is the shared ``(scope, external_id, provider) → internal_id``
store consumed by every backend that cares about session continuity
across platforms (OpenClaw today, Codex and future adapters next).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpak.services.routing_service.session_mapper import (
    SessionMap,
    SessionRecord,
)


@pytest.fixture
def mapper(tmp_path: Path) -> SessionMap:
    return SessionMap(db_path=tmp_path / "session_map.db")


# ── Roundtrip ───────────────────────────────────────────────────────────────


def test_get_returns_none_for_unknown_triple(mapper):
    assert mapper.get("openclaw", "sess-1", "tokenpak-claude-code") is None


def test_set_then_get_roundtrip(mapper):
    ok = mapper.set(
        scope="openclaw",
        external_id="sess-1",
        provider="tokenpak-claude-code",
        internal_id="claude-uuid-xyz",
        metadata={"model": "claude-sonnet-4-6"},
    )
    assert ok is True
    rec = mapper.get("openclaw", "sess-1", "tokenpak-claude-code")
    assert isinstance(rec, SessionRecord)
    assert rec.scope == "openclaw"
    assert rec.external_id == "sess-1"
    assert rec.provider == "tokenpak-claude-code"
    assert rec.internal_id == "claude-uuid-xyz"
    assert rec.metadata == {"model": "claude-sonnet-4-6"}


def test_primary_key_is_triple_not_external_id_alone(mapper):
    mapper.set("openclaw", "sess-1", "tokenpak-claude-code", "uuid-a")
    mapper.set("openclaw", "sess-1", "tokenpak-anthropic", "uuid-b")
    assert mapper.get("openclaw", "sess-1", "tokenpak-claude-code").internal_id == "uuid-a"
    assert mapper.get("openclaw", "sess-1", "tokenpak-anthropic").internal_id == "uuid-b"


def test_set_is_upsert(mapper):
    mapper.set("openclaw", "sess-1", "tokenpak-claude-code", "uuid-old")
    mapper.set("openclaw", "sess-1", "tokenpak-claude-code", "uuid-new")
    rec = mapper.get("openclaw", "sess-1", "tokenpak-claude-code")
    assert rec.internal_id == "uuid-new"


def test_delete_removes_mapping(mapper):
    mapper.set("openclaw", "sess-1", "tokenpak-claude-code", "uuid")
    assert mapper.delete("openclaw", "sess-1", "tokenpak-claude-code") is True
    assert mapper.get("openclaw", "sess-1", "tokenpak-claude-code") is None


def test_delete_returns_false_for_missing(mapper):
    assert mapper.delete("openclaw", "nonexistent", "provider") is False


def test_count_reflects_actual_rows(mapper):
    assert mapper.count() == 0
    mapper.set("openclaw", "s1", "p1", "i1")
    mapper.set("openclaw", "s2", "p1", "i2")
    mapper.set("codex", "s1", "p1", "i3")
    assert mapper.count() == 3


# ── Liveness (last_used_at) ─────────────────────────────────────────────────


def test_get_touches_last_used_at(mapper):
    mapper.set("openclaw", "sess-1", "tokenpak-claude-code", "uuid")
    rec1 = mapper.get("openclaw", "sess-1", "tokenpak-claude-code")
    # Artificially age the row by sleeping briefly then reading.
    import time as _time

    _time.sleep(0.05)
    rec2 = mapper.get("openclaw", "sess-1", "tokenpak-claude-code")
    assert rec2.last_used_at >= rec1.last_used_at


def test_prune_older_than_removes_stale(mapper):
    mapper.set("openclaw", "old", "p", "i-old")
    mapper.set("openclaw", "new", "p", "i-new")
    # Force old row to look stale by setting last_used_at far in the past.
    import sqlite3 as _sqlite3

    with _sqlite3.connect(str(mapper._db_path)) as c:
        c.execute(
            "UPDATE session_map SET last_used_at=0 WHERE external_id=?", ("old",)
        )
    # Prune anything older than 1 second — only the zeroed row qualifies.
    removed = mapper.prune_older_than(1.0)
    assert removed == 1
    assert mapper.get("openclaw", "old", "p") is None
    assert mapper.get("openclaw", "new", "p") is not None


# ── Corrupt-db recovery ─────────────────────────────────────────────────────


def test_constructor_recovers_from_corrupt_db(tmp_path: Path):
    # Plant a non-SQLite file at the target path.
    db_path = tmp_path / "session_map.db"
    db_path.write_bytes(b"not a database, just garbage")
    # Construction must NOT raise; a fresh db replaces the corrupt file.
    mapper = SessionMap(db_path=db_path)
    mapper.set("openclaw", "sess-1", "provider", "uuid")
    assert mapper.get("openclaw", "sess-1", "provider").internal_id == "uuid"
    # Corrupt file got quarantined (renamed with .corrupt-<ts>.db suffix).
    corrupted = list(tmp_path.glob("session_map.corrupt-*.db"))
    assert len(corrupted) == 1


# ── Opt-out env ─────────────────────────────────────────────────────────────


def test_env_disables_all_operations(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_SESSION_MAPPER", "0")
    m = SessionMap(db_path=tmp_path / "session_map.db")
    assert m.set("openclaw", "s", "p", "i") is False
    assert m.get("openclaw", "s", "p") is None
    assert m.delete("openclaw", "s", "p") is False
    assert m.prune_older_than(0) == 0
    assert m.count() == 0


def test_env_enabled_by_default(mapper, monkeypatch):
    monkeypatch.delenv("TOKENPAK_SESSION_MAPPER", raising=False)
    assert mapper.set("openclaw", "s", "p", "i") is True
    assert mapper.get("openclaw", "s", "p") is not None


# ── Singleton ───────────────────────────────────────────────────────────────


def test_singleton_returns_same_instance():
    from tokenpak.services.routing_service.session_mapper import get_session_mapper

    a = get_session_mapper()
    b = get_session_mapper()
    assert a is b
