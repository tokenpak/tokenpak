"""Regression coverage for the pakplan recall-store drift cleanup.

The pakplan preview/report path historically read its Pak metadata from a
stale ``journal.db`` and joined against the old ``pak_reasons`` / ``pak_risks``
tables — names that predate the canonical recall store. The recall store now
owns ``recall.db`` with ``pak_reason_codes`` and ``pak_risk_flags``.

These tests pin the current names so the drift cannot silently return:

1. ``_recall_db()`` resolves to ``recall.db`` (not ``journal.db``).
2. ``_query_paks()`` reads reason/risk metadata from the canonical
   ``pak_reason_codes`` / ``pak_risk_flags`` tables of a real recall store.
3. The module source carries no stale ``journal.db`` / ``pak_reasons`` /
   ``pak_risks`` literals (mirrors the packet's regression-acceptance grep).
"""

from __future__ import annotations

import re
from pathlib import Path

from tokenpak.cli.commands import pakplan
from tokenpak.companion.recall.store import (
    ReasonCodeEntry,
    RecallStore,
    RiskFlagEntry,
)


def _seed_recall_db(path: Path) -> None:
    store = RecallStore.open(path)
    try:
        store.upsert_pak(
            pak_id="vault://block/example",
            pak_type="vault",
            source_type="doc",
            authority="test",
            title="Example Pak",
            content_hash="deadbeef",
            summary="example summary",
        )
        store.set_pak_reason_codes(
            "vault://block/example",
            [ReasonCodeEntry("current_task", 0.9)],
        )
        store.set_pak_risk_flags(
            "vault://block/example",
            [RiskFlagEntry("mandatory_context_missing", "warn")],
        )
    finally:
        store.close()


def test_recall_db_resolves_to_recall_not_journal() -> None:
    db = pakplan._recall_db()
    assert db is not None
    assert db.name == "recall.db"
    assert "journal.db" not in str(db)


def test_query_paks_reads_canonical_reason_and_risk_tables(tmp_path: Path) -> None:
    db = tmp_path / "recall.db"
    _seed_recall_db(db)

    rows = pakplan._query_paks(db, limit=10)

    assert len(rows) == 1
    row = rows[0]
    assert row["pak_id"] == "vault://block/example"
    # Joined from pak_reason_codes / pak_risk_flags — not the old tables.
    assert row["_reason_codes"] == ["current_task"]
    assert row["_risk_flags"] == ["mandatory_context_missing"]


def test_query_pak_by_id_uses_canonical_store(tmp_path: Path) -> None:
    db = tmp_path / "recall.db"
    _seed_recall_db(db)

    row = pakplan._query_pak_by_id(db, "vault://block/example")

    assert row is not None
    assert row["_reason_codes"] == ["current_task"]
    assert row["_risk_flags"] == ["mandatory_context_missing"]


def test_source_has_no_stale_drift_literals() -> None:
    src = Path(pakplan.__file__).read_text(encoding="utf-8")
    # The recall db source and join tables must use the current names.
    assert "journal.db" not in src
    assert not re.search(r'"pak_reasons"', src)
    assert not re.search(r'"pak_risks"', src)
    assert '"pak_reason_codes"' in src
    assert '"pak_risk_flags"' in src
    assert '"recall.db"' in src
