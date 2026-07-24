# SPDX-License-Identifier: Apache-2.0
"""FTS5 shadow-table triggers introduced in migration v2.

The triggers maintain ``paks_fts`` in lockstep with ``paks``. These tests
fire the triggers via both the ``RecallStore.upsert_pak`` write path and
direct SQL on the ``paks`` table to prove the contract is at the schema
layer, not at the API layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpak.companion.recall import RecallStore


def _fts_pak_ids(store: RecallStore) -> list[str]:
    rows = store.conn.execute("SELECT pak_id FROM paks_fts").fetchall()
    return sorted(r[0] for r in rows)


def _match(store: RecallStore, term: str) -> list[str]:
    rows = store.conn.execute(
        "SELECT pak_id FROM paks_fts WHERE paks_fts MATCH ?", (term,)
    ).fetchall()
    return sorted(r[0] for r in rows)


def _full_row() -> dict[str, str]:
    return {
        "pak_id": "vault://block/credential",
        "pak_type": "vault",
        "source_type": "doc",
        "authority": "llm_generated",
        "title": "router-not-vault credential architecture",
        "content_hash": "ab" * 16,
        "summary": "Single refresh-owner pattern across providers.",
        "project": "tokenpak",
        "topic": "creds",
    }


# ----- Trigger expectations on the schema -----------------------------------


def test_v2_triggers_are_registered(tmp_path: Path, require_fts5: None) -> None:
    """The three v2 triggers are present on a freshly-opened store."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        names = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert {"paks_ai_fts", "paks_au_fts", "paks_ad_fts"}.issubset(names)


# ----- Insert path (via upsert_pak) ----------------------------------------


def test_upsert_pak_populates_fts(tmp_path: Path, require_fts5: None) -> None:
    """``upsert_pak`` inserts a paks row → FTS row appears via the trigger."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**_full_row())
        ids = _fts_pak_ids(store)
        hits = _match(store, "credential")
    assert ids == ["vault://block/credential"]
    assert hits == ["vault://block/credential"]


# ----- Insert path (via raw SQL) -------------------------------------------


def test_raw_insert_into_paks_populates_fts(tmp_path: Path, require_fts5: None) -> None:
    """A direct ``INSERT INTO paks`` also fires the AFTER INSERT trigger."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.conn.execute(
            "INSERT INTO paks ("
            "pak_id, pak_type, source_type, authority, "
            "title, summary, content_hash, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "vault://block/raw",
                "vault",
                "doc",
                "llm_generated",
                "raw insert with token",
                "raw summary",
                "11" * 16,
                "2026-05-11T00:00:00Z",
                "2026-05-11T00:00:00Z",
            ),
        )
        store.conn.commit()
        ids = _fts_pak_ids(store)
        hits = _match(store, "token")
    assert ids == ["vault://block/raw"]
    assert hits == ["vault://block/raw"]


# ----- Update path ---------------------------------------------------------


def test_upsert_body_change_updates_fts_content(tmp_path: Path, require_fts5: None) -> None:
    """Updating title/summary via ``upsert_pak`` rewrites the FTS row."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**_full_row())
        revised = _full_row()
        revised["content_hash"] = "cd" * 16
        revised["title"] = "router-not-vault — revised"
        revised["summary"] = "completely different summary mentioning rotation"
        store.upsert_pak(**revised)
        ids = _fts_pak_ids(store)
        old_hits = _match(store, "Single")
        new_hits = _match(store, "rotation")
    assert ids == ["vault://block/credential"]
    # The original summary mentioned "Single"; after replacement it should not.
    assert old_hits == []
    assert new_hits == ["vault://block/credential"]


def test_raw_update_of_title_only_rewrites_fts(tmp_path: Path, require_fts5: None) -> None:
    """A direct ``UPDATE paks SET title=?`` fires the column-filtered trigger."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**_full_row())
        store.conn.execute(
            "UPDATE paks SET title = ? WHERE pak_id = ?",
            ("new heading only", "vault://block/credential"),
        )
        store.conn.commit()
        hits_new = _match(store, "heading")
        # ``router`` would still match the new title's "router…heading"
        # phrasing if FTS hadn't been rewritten, but the original token
        # ``architecture`` would not — use the more distinctive token.
        hits_old = _match(store, "architecture")
    assert hits_new == ["vault://block/credential"]
    assert hits_old == []


def test_unrelated_column_update_does_not_rewrite_fts(tmp_path: Path, require_fts5: None) -> None:
    """Updating only ``project`` must not delete the FTS row.

    The ``AFTER UPDATE OF title, summary`` filter means an unrelated
    update is silent for the FTS shadow. The row is still searchable.
    """
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**_full_row())
        store.conn.execute(
            "UPDATE paks SET project = ? WHERE pak_id = ?",
            ("different-project", "vault://block/credential"),
        )
        store.conn.commit()
        hits = _match(store, "credential")
    assert hits == ["vault://block/credential"]


# ----- Delete path ---------------------------------------------------------


def test_delete_from_paks_removes_fts_row(tmp_path: Path, require_fts5: None) -> None:
    """``DELETE FROM paks WHERE pak_id = ?`` fires the AFTER DELETE trigger."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.upsert_pak(**_full_row())
        assert _fts_pak_ids(store) == ["vault://block/credential"]
        store.conn.execute(
            "DELETE FROM paks WHERE pak_id = ?",
            ("vault://block/credential",),
        )
        store.conn.commit()
        ids = _fts_pak_ids(store)
        hits = _match(store, "credential")
    assert ids == []
    assert hits == []


# ----- Multi-row sanity ----------------------------------------------------


def test_match_isolates_rows(tmp_path: Path, require_fts5: None) -> None:
    """FTS rows are independent; deleting one row leaves siblings searchable."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        a = _full_row()
        b = _full_row()
        b["pak_id"] = "vault://block/other"
        b["title"] = "unrelated cache topic"
        b["summary"] = "different content"
        b["content_hash"] = "ef" * 16
        store.upsert_pak(**a)
        store.upsert_pak(**b)

        store.conn.execute("DELETE FROM paks WHERE pak_id = ?", ("vault://block/credential",))
        store.conn.commit()
        ids = _fts_pak_ids(store)
        hits = _match(store, "cache")
    assert ids == ["vault://block/other"]
    assert hits == ["vault://block/other"]
