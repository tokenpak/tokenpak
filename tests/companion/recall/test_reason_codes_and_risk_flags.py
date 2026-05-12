# SPDX-License-Identifier: Apache-2.0
"""``RecallStore`` reason-code + risk-flag join surface (v3 migration).

The store-side write API for the Std 32 §5.4 / §5.5 join tables.
OSS exposes / persists / inspects / exports / validates the data;
OSS does *not* implement an assembly-refusal path on ``severity="block"``
(that enforcement is Pro Phase 3 Context Package builder behaviour per
Std 32 §13.1 Decision #11).

Covered:
- Round-trip (set → get) for reason-codes and risk-flags.
- DELETE-then-INSERT idempotency: calling ``set_*`` twice with the same
  payload leaves the table in the same final state.
- Empty payload clears the prior set.
- FK cascade: deleting a row from ``paks`` removes its join rows.
- SQL CHECK constraints: severity enum, weight range.
- Validation: empty ``pak_id``, empty code/flag, duplicate inputs.
- Unknown ``pak_id`` returns ``[]`` rather than raising.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tokenpak.companion.recall import (
    ReasonCodeEntry,
    RecallStore,
    RISK_FLAG_SEVERITIES,
    RiskFlagEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BASE_ROW = {
    "pak_id": "vault://block/auth-pattern",
    "pak_type": "vault",
    "source_type": "doc",
    "authority": "llm_generated",
    "title": "router-not-vault credential architecture",
    "content_hash": "0123456789abcdef" * 4,
    "summary": "Single-refresh-owner invariant across providers.",
    "project": "tokenpak",
    "topic": "creds",
}


def _seed_one_pak(store: RecallStore, pak_id: str = _BASE_ROW["pak_id"]) -> str:
    """Insert a single Pak row so subsequent FK-bound writes succeed."""
    row = dict(_BASE_ROW)
    row["pak_id"] = pak_id
    store.upsert_pak(**row)
    return pak_id


# ---------------------------------------------------------------------------
# Reason-code round-trip
# ---------------------------------------------------------------------------


class TestReasonCodes:
    def test_round_trip_single_entry(self, tmp_path: Path, require_fts5: None) -> None:
        """``set`` then ``get`` returns the entry back unchanged."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(
                pid,
                [ReasonCodeEntry(reason_code="current_task", weight=1.0)],
            )
            got = store.get_pak_reason_codes(pid)
        assert got == [ReasonCodeEntry(reason_code="current_task", weight=1.0)]

    def test_get_returns_sorted_ascending(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """The read helper returns entries in deterministic ascending order."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(
                pid,
                [
                    ReasonCodeEntry(reason_code="user_pinned", weight=1.0),
                    ReasonCodeEntry(reason_code="current_task", weight=0.9),
                    ReasonCodeEntry(reason_code="mandatory", weight=1.0),
                ],
            )
            got = store.get_pak_reason_codes(pid)
        assert [e.reason_code for e in got] == [
            "current_task",
            "mandatory",
            "user_pinned",
        ]

    def test_set_is_idempotent(self, tmp_path: Path, require_fts5: None) -> None:
        """Calling ``set`` twice with the same payload yields the same state."""
        db_path = tmp_path / "recall.db"
        codes = [
            ReasonCodeEntry(reason_code="standard_applies", weight=1.0),
            ReasonCodeEntry(reason_code="recent_failure_link", weight=0.5),
        ]
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(pid, codes)
            first = store.get_pak_reason_codes(pid)
            store.set_pak_reason_codes(pid, codes)
            second = store.get_pak_reason_codes(pid)
        assert first == second

    def test_set_replaces_prior_set(self, tmp_path: Path, require_fts5: None) -> None:
        """A second ``set`` with different codes replaces the prior set."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(
                pid,
                [ReasonCodeEntry(reason_code="current_task")],
            )
            store.set_pak_reason_codes(
                pid,
                [ReasonCodeEntry(reason_code="standard_applies")],
            )
            got = store.get_pak_reason_codes(pid)
        assert [e.reason_code for e in got] == ["standard_applies"]

    def test_empty_payload_clears(self, tmp_path: Path, require_fts5: None) -> None:
        """Passing an empty sequence clears all reason codes for ``pak_id``."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(
                pid,
                [ReasonCodeEntry(reason_code="current_task")],
            )
            assert store.get_pak_reason_codes(pid) != []
            store.set_pak_reason_codes(pid, [])
            assert store.get_pak_reason_codes(pid) == []

    def test_unknown_pak_id_returns_empty(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """``get`` for an unknown ``pak_id`` returns ``[]``."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            _seed_one_pak(store)
            assert store.get_pak_reason_codes("vault://unknown") == []

    def test_blank_pak_id_returns_empty(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """``get`` with blank/whitespace ``pak_id`` returns ``[]`` (no DB round-trip)."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            assert store.get_pak_reason_codes("   ") == []

    def test_set_rejects_empty_pak_id(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            with pytest.raises(ValueError) as exc:
                store.set_pak_reason_codes(
                    "  ",
                    [ReasonCodeEntry(reason_code="current_task")],
                )
        assert "pak_id" in str(exc.value)

    def test_set_rejects_blank_code(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            with pytest.raises(ValueError) as exc:
                store.set_pak_reason_codes(
                    pid,
                    [ReasonCodeEntry(reason_code="   ", weight=1.0)],
                )
        assert "reason_code" in str(exc.value)

    def test_set_rejects_duplicate_code(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """A duplicate ``reason_code`` within a single call is rejected early."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            with pytest.raises(ValueError) as exc:
                store.set_pak_reason_codes(
                    pid,
                    [
                        ReasonCodeEntry(reason_code="current_task"),
                        ReasonCodeEntry(reason_code="current_task", weight=0.5),
                    ],
                )
        assert "duplicate" in str(exc.value).lower()

    @pytest.mark.parametrize("bad_weight", [-0.1, 1.1, 2.0, -1.0])
    def test_set_rejects_out_of_range_weight(
        self, tmp_path: Path, require_fts5: None, bad_weight: float
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            with pytest.raises(ValueError) as exc:
                store.set_pak_reason_codes(
                    pid,
                    [ReasonCodeEntry(reason_code="current_task", weight=bad_weight)],
                )
        assert "weight" in str(exc.value).lower()

    def test_unknown_pak_id_foreign_key_violation(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """Writing for a non-existent ``pak_id`` raises IntegrityError."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            with pytest.raises(sqlite3.IntegrityError):
                store.set_pak_reason_codes(
                    "vault://missing",
                    [ReasonCodeEntry(reason_code="current_task")],
                )

    def test_failed_set_rolls_back(self, tmp_path: Path, require_fts5: None) -> None:
        """A FK violation must not leave partial rows behind."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(
                pid,
                [ReasonCodeEntry(reason_code="current_task")],
            )
            with pytest.raises(sqlite3.IntegrityError):
                store.set_pak_reason_codes(
                    "vault://missing",
                    [ReasonCodeEntry(reason_code="current_task")],
                )
            # Original row is untouched.
            assert [e.reason_code for e in store.get_pak_reason_codes(pid)] == [
                "current_task"
            ]

    def test_fk_cascade_on_paks_delete(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """Deleting a ``paks`` row cascades to ``pak_reason_codes``."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_reason_codes(
                pid,
                [ReasonCodeEntry(reason_code="current_task")],
            )
            store.conn.execute("DELETE FROM paks WHERE pak_id = ?", (pid,))
            store.conn.commit()
            n = store.conn.execute(
                "SELECT COUNT(*) FROM pak_reason_codes WHERE pak_id = ?",
                (pid,),
            ).fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# Risk-flag round-trip
# ---------------------------------------------------------------------------


class TestRiskFlags:
    def test_severities_constant_matches_check_constraint(self) -> None:
        """The exported set must match the schema CHECK exactly.

        The CHECK constraint is the source of truth; ``RISK_FLAG_SEVERITIES``
        exists so callers / tests can probe the same set without crafting
        a DB and triggering a constraint failure.
        """
        assert RISK_FLAG_SEVERITIES == frozenset({"info", "warn", "block"})

    def test_round_trip_single_flag(self, tmp_path: Path, require_fts5: None) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [RiskFlagEntry(risk_flag="mandatory_context_missing", severity="block")],
            )
            got = store.get_pak_risk_flags(pid)
        assert got == [
            RiskFlagEntry(risk_flag="mandatory_context_missing", severity="block")
        ]

    def test_get_returns_sorted_ascending(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [
                    RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn"),
                    RiskFlagEntry(risk_flag="cache_sensitive_ordering", severity="info"),
                    RiskFlagEntry(risk_flag="mandatory_context_missing", severity="block"),
                ],
            )
            got = store.get_pak_risk_flags(pid)
        assert [e.risk_flag for e in got] == [
            "cache_sensitive_ordering",
            "mandatory_context_missing",
            "raw_logs_deferred",
        ]

    def test_set_is_idempotent(self, tmp_path: Path, require_fts5: None) -> None:
        db_path = tmp_path / "recall.db"
        flags = [
            RiskFlagEntry(risk_flag="mandatory_context_missing", severity="block"),
            RiskFlagEntry(risk_flag="manual_review_recommended", severity="warn"),
        ]
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(pid, flags)
            first = store.get_pak_risk_flags(pid)
            store.set_pak_risk_flags(pid, flags)
            second = store.get_pak_risk_flags(pid)
        assert first == second

    def test_set_replaces_prior_set(self, tmp_path: Path, require_fts5: None) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn")],
            )
            store.set_pak_risk_flags(
                pid,
                [
                    RiskFlagEntry(
                        risk_flag="mandatory_context_missing", severity="block"
                    )
                ],
            )
            got = store.get_pak_risk_flags(pid)
        assert [e.risk_flag for e in got] == ["mandatory_context_missing"]

    def test_empty_payload_clears(self, tmp_path: Path, require_fts5: None) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [RiskFlagEntry(risk_flag="cache_sensitive_ordering", severity="info")],
            )
            assert store.get_pak_risk_flags(pid) != []
            store.set_pak_risk_flags(pid, [])
            assert store.get_pak_risk_flags(pid) == []

    def test_unknown_pak_id_returns_empty(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            _seed_one_pak(store)
            assert store.get_pak_risk_flags("vault://unknown") == []

    def test_set_rejects_unknown_severity(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """A ``severity`` outside ``{info, warn, block}`` is rejected before write."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            with pytest.raises(ValueError) as exc:
                store.set_pak_risk_flags(
                    pid,
                    [RiskFlagEntry(risk_flag="raw_logs_deferred", severity="critical")],
                )
        assert "severity" in str(exc.value).lower()

    def test_set_rejects_blank_flag(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            with pytest.raises(ValueError):
                store.set_pak_risk_flags(
                    pid,
                    [RiskFlagEntry(risk_flag="   ", severity="info")],
                )

    def test_set_rejects_duplicate_flag(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            with pytest.raises(ValueError) as exc:
                store.set_pak_risk_flags(
                    pid,
                    [
                        RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn"),
                        RiskFlagEntry(risk_flag="raw_logs_deferred", severity="info"),
                    ],
                )
        assert "duplicate" in str(exc.value).lower()

    def test_set_rejects_empty_pak_id(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            with pytest.raises(ValueError):
                store.set_pak_risk_flags(
                    "",
                    [RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn")],
                )

    def test_unknown_pak_id_foreign_key_violation(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            with pytest.raises(sqlite3.IntegrityError):
                store.set_pak_risk_flags(
                    "vault://missing",
                    [RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn")],
                )

    def test_fk_cascade_on_paks_delete(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn")],
            )
            store.conn.execute("DELETE FROM paks WHERE pak_id = ?", (pid,))
            store.conn.commit()
            n = store.conn.execute(
                "SELECT COUNT(*) FROM pak_risk_flags WHERE pak_id = ?",
                (pid,),
            ).fetchone()[0]
        assert n == 0


# ---------------------------------------------------------------------------
# OSS / Pro boundary — Std 32 §13.1 Decision #11 + §5.5 severity semantics
# ---------------------------------------------------------------------------


class TestSeverityBlockOssTransparency:
    """OSS exposes / persists / inspects / exports / validates ``block``
    severity data without refusing assembly.

    The Pro Phase 3 Context Package builder is the authoritative refusal
    site; OSS is a transparent data plane (Std 32 §13.1 Decision #11).
    The store has no concept of a "refusal" path — these tests pin that
    invariant: a ``block`` flag is stored, returned, and counted the same
    as any other severity.
    """

    def test_block_severity_persists_and_round_trips(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [
                    RiskFlagEntry(
                        risk_flag="mandatory_context_missing", severity="block"
                    )
                ],
            )
            got = store.get_pak_risk_flags(pid)
        assert len(got) == 1
        assert got[0].severity == "block"

    def test_block_does_not_prevent_other_flag_persistence(
        self, tmp_path: Path, require_fts5: None
    ) -> None:
        """A ``block`` row does not cause the call to abort or drop other rows."""
        db_path = tmp_path / "recall.db"
        with RecallStore.open(db_path) as store:
            pid = _seed_one_pak(store)
            store.set_pak_risk_flags(
                pid,
                [
                    RiskFlagEntry(risk_flag="mandatory_context_missing", severity="block"),
                    RiskFlagEntry(risk_flag="raw_logs_deferred", severity="warn"),
                ],
            )
            got = {e.risk_flag for e in store.get_pak_risk_flags(pid)}
        assert got == {"mandatory_context_missing", "raw_logs_deferred"}
