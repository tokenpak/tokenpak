# SPDX-License-Identifier: Apache-2.0
"""``RecallStore.upsert_pak`` — idempotency and body-conflict behaviour."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tokenpak.companion.recall import RecallStore

_PID = "vault://block/idempotent"


def _row() -> dict[str, str]:
    return {
        "pak_id": _PID,
        "pak_type": "vault",
        "source_type": "doc",
        "authority": "llm_generated",
        "title": "idempotency under content equality",
        "content_hash": "aa" * 16,
        "summary": "first summary",
        "project": "tokenpak",
        "topic": "recall",
    }


def test_upsert_same_content_hash_is_idempotent_count(tmp_path: Path, require_fts5: None) -> None:
    """Calling twice with identical content_hash yields exactly one row."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(now="2026-05-11T10:00:00Z", **_row())
        result = store.upsert_pak(now="2026-05-11T11:00:00Z", **_row())
        n = store.conn.execute("SELECT COUNT(*) FROM paks").fetchone()[0]
    assert n == 1
    assert result.inserted is False
    assert result.body_changed is False


def test_upsert_same_content_hash_preserves_created_at_bumps_updated_at(
    tmp_path: Path, require_fts5: None
) -> None:
    """A second upsert keeps ``created_at`` and bumps ``updated_at``."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(now="2026-05-11T10:00:00Z", **_row())
        store.upsert_pak(now="2026-05-11T11:00:00Z", **_row())
        created, updated = store.conn.execute(
            "SELECT created_at, updated_at FROM paks WHERE pak_id = ?",
            (_PID,),
        ).fetchone()
    assert created == "2026-05-11T10:00:00Z"
    assert updated == "2026-05-11T11:00:00Z"


def test_upsert_idempotency_holds_across_reopen(tmp_path: Path, require_fts5: None) -> None:
    """Re-opening the store keeps the single-row invariant."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as first:
        first.upsert_pak(**_row())
    with RecallStore.open(db_path) as second:
        second.upsert_pak(**_row())
        n = second.conn.execute("SELECT COUNT(*) FROM paks").fetchone()[0]
    assert n == 1


def test_upsert_body_changed_replaces_metadata_and_warns(
    tmp_path: Path, require_fts5: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A differing content_hash for the same pak_id replaces metadata + warns."""
    db_path = tmp_path / "recall.db"
    initial = _row()
    new = _row()
    new["content_hash"] = "bb" * 16
    new["title"] = "router-not-vault — revised"
    new["summary"] = "second summary"

    with RecallStore.open(db_path) as store:
        store.upsert_pak(now="2026-05-11T10:00:00Z", **initial)
        with caplog.at_level(logging.WARNING, logger="tokenpak.companion.recall.store"):
            result = store.upsert_pak(now="2026-05-11T11:00:00Z", **new)

        n = store.conn.execute("SELECT COUNT(*) FROM paks").fetchone()[0]
        title, summary, content_hash, created, updated = store.conn.execute(
            "SELECT title, summary, content_hash, created_at, updated_at "
            "FROM paks WHERE pak_id = ?",
            (_PID,),
        ).fetchone()

    assert n == 1
    assert result.inserted is False
    assert result.body_changed is True
    assert title == "router-not-vault — revised"
    assert summary == "second summary"
    assert content_hash == "bb" * 16
    assert created == "2026-05-11T10:00:00Z"
    assert updated == "2026-05-11T11:00:00Z"

    # Warn-level log must mention the pak_id; the digest is short-rendered.
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warn_records, "expected at least one WARNING record"
    joined = "\n".join(r.getMessage() for r in warn_records)
    assert _PID in joined
    assert "content_hash changed" in joined


def test_upsert_idempotent_call_does_not_emit_warning(
    tmp_path: Path, require_fts5: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A no-change re-upsert is silent at WARNING level."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**_row())
        with caplog.at_level(logging.WARNING, logger="tokenpak.companion.recall.store"):
            store.upsert_pak(**_row())
    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warn_records == []


def test_upsert_body_change_optional_fields_can_be_cleared(
    tmp_path: Path, require_fts5: None
) -> None:
    """Replacement allows clearing previously-set optional fields to NULL."""
    db_path = tmp_path / "recall.db"
    initial = _row()
    cleared = _row()
    cleared["content_hash"] = "cc" * 16
    cleared["project"] = None
    cleared["topic"] = None
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**initial)
        store.upsert_pak(**cleared)
        project, topic = store.conn.execute(
            "SELECT project, topic FROM paks WHERE pak_id = ?", (_PID,)
        ).fetchone()
    assert project is None
    assert topic is None
