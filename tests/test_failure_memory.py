"""Tests for tokenpak._internal.agentic.failure_memory

Coverage:
  T1 — Signature matching works (pattern found)
  T2 — Recipe returns highest-confidence match among multiple candidates
  T3 — Learning loop increments success_count and raises confidence
  T4 — Learning loop increments failure_count and lowers confidence
  T5 — New (unknown) signatures created for unseen errors
  T6 — Persistence survives restart (reload from disk)
  T7 — No false matches on unrelated errors
  T8 — Validated flag set after N_VALIDATE_SUCCESSES
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from tokenpak._internal.agentic.failure_memory import (
    FailureMemoryDB,
    FailureSignature,
    N_VALIDATE_SUCCESSES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> FailureMemoryDB:
    """Return a fresh FailureMemoryDB backed by a temp file."""
    return FailureMemoryDB(storage_path=tmp_path / "sigs.json")


@pytest.fixture
def sig_pg() -> FailureSignature:
    return FailureSignature(
        signature_id="pg_conn_refused",
        error_class="port_bind_failure",
        error_pattern=r"connection refused.*port 5432",
        root_causes=["postgres not running"],
        repair_recipe=["systemctl start postgresql", "pg_isready"],
        confidence=0.8,
    )


@pytest.fixture
def sig_auth() -> FailureSignature:
    return FailureSignature(
        signature_id="auth_401",
        error_class="auth_error",
        error_pattern=r"(401|unauthorized|authentication failed)",
        root_causes=["expired token", "wrong credentials"],
        repair_recipe=["refresh api key", "check ~/.tokenpak/config.json"],
        confidence=0.6,
    )


# ---------------------------------------------------------------------------
# T1 — Signature matching works
# ---------------------------------------------------------------------------


def test_match_known_pattern(tmp_db: FailureMemoryDB, sig_pg: FailureSignature) -> None:
    """T1: A known error pattern is found and returned."""
    tmp_db.add(sig_pg)

    result = tmp_db.match("Connection refused: could not connect to port 5432")
    assert result is not None
    assert result.signature_id == "pg_conn_refused"
    assert result.repair_recipe == ["systemctl start postgresql", "pg_isready"]


# ---------------------------------------------------------------------------
# T2 — Recipe returns highest-confidence match
# ---------------------------------------------------------------------------


def test_match_returns_highest_confidence(
    tmp_db: FailureMemoryDB,
    sig_pg: FailureSignature,
    sig_auth: FailureSignature,
) -> None:
    """T2: When multiple patterns match, highest confidence wins."""
    # Give auth_401 a higher confidence and a broader pattern that also
    # matches our test string
    sig_auth.error_pattern = r"refused|port 5432"
    sig_auth.confidence = 0.95
    sig_pg.confidence = 0.6

    tmp_db.add(sig_pg)
    tmp_db.add(sig_auth)

    result = tmp_db.match("Connection refused: port 5432 unreachable")
    assert result is not None
    assert result.signature_id == "auth_401"  # higher confidence


# ---------------------------------------------------------------------------
# T3 — Learning loop: success path
# ---------------------------------------------------------------------------


def test_learning_success_increments_count(
    tmp_db: FailureMemoryDB, sig_pg: FailureSignature
) -> None:
    """T3: Recording a successful repair increments success_count and confidence."""
    sig_pg.confidence = 0.5
    tmp_db.add(sig_pg)

    updated = tmp_db.record_repair_outcome("pg_conn_refused", success=True)
    assert updated is not None
    assert updated.success_count == 1
    assert updated.confidence > 0.5


# ---------------------------------------------------------------------------
# T4 — Learning loop: failure path
# ---------------------------------------------------------------------------


def test_learning_failure_decrements_count(
    tmp_db: FailureMemoryDB, sig_pg: FailureSignature
) -> None:
    """T4: Recording a failed repair increments failure_count and lowers confidence."""
    sig_pg.confidence = 0.8
    tmp_db.add(sig_pg)

    updated = tmp_db.record_repair_outcome("pg_conn_refused", success=False)
    assert updated is not None
    assert updated.failure_count == 1
    assert updated.confidence < 0.8


# ---------------------------------------------------------------------------
# T5 — New signature created for unknown errors
# ---------------------------------------------------------------------------


def test_unknown_error_creates_new_signature(tmp_db: FailureMemoryDB) -> None:
    """T5: An unseen error pattern creates a new stub signature."""
    initial_count = tmp_db.count()
    result = tmp_db.match("TOTALLY_UNRECOGNISED error xyz-789 boom")
    # match() returns None for unknown errors but records a stub
    assert result is None
    assert tmp_db.count() == initial_count + 1

    # The stub should have an auto_ prefix and low confidence
    stubs = [s for s in tmp_db.list_all() if s.signature_id.startswith("auto_")]
    assert len(stubs) == 1
    assert stubs[0].confidence < 0.3


# ---------------------------------------------------------------------------
# T6 — Persistence survives restart
# ---------------------------------------------------------------------------


def test_persistence_survives_reload(
    tmp_path: Path, sig_pg: FailureSignature
) -> None:
    """T6: Signatures written to disk are re-loaded by a new DB instance."""
    storage = tmp_path / "sigs.json"

    db1 = FailureMemoryDB(storage_path=storage)
    db1.add(sig_pg)
    db1.record_repair_outcome("pg_conn_refused", success=True)

    # New instance reads same file
    db2 = FailureMemoryDB(storage_path=storage)
    loaded = db2.get("pg_conn_refused")
    assert loaded is not None
    assert loaded.success_count == 1
    assert loaded.repair_recipe == ["systemctl start postgresql", "pg_isready"]


# ---------------------------------------------------------------------------
# T7 — No false matches on unrelated errors
# ---------------------------------------------------------------------------


def test_no_false_match_on_unrelated_error(
    tmp_db: FailureMemoryDB,
    sig_pg: FailureSignature,
    sig_auth: FailureSignature,
) -> None:
    """T7: Completely unrelated error text returns None (no false positives)."""
    tmp_db.add(sig_pg)
    tmp_db.add(sig_auth)

    initial_count = tmp_db.count()
    result = tmp_db.match("disk quota exceeded on /dev/sda1 inode limit reached")
    # Neither postgres nor auth patterns match
    assert result is None


# ---------------------------------------------------------------------------
# T8 — validated flag set after N successes
# ---------------------------------------------------------------------------


def test_validated_after_n_successes(
    tmp_db: FailureMemoryDB, sig_pg: FailureSignature
) -> None:
    """T8: Signature becomes validated after N_VALIDATE_SUCCESSES successes."""
    sig_pg.confidence = 0.5
    tmp_db.add(sig_pg)

    for _ in range(N_VALIDATE_SUCCESSES):
        updated = tmp_db.record_repair_outcome("pg_conn_refused", success=True)

    assert updated is not None
    assert updated.validated is True
    assert updated.success_count == N_VALIDATE_SUCCESSES


# ---------------------------------------------------------------------------
# Bonus: CRUD round-trip
# ---------------------------------------------------------------------------


def test_crud_delete(tmp_db: FailureMemoryDB, sig_pg: FailureSignature) -> None:
    tmp_db.add(sig_pg)
    assert tmp_db.get("pg_conn_refused") is not None
    removed = tmp_db.delete("pg_conn_refused")
    assert removed is True
    assert tmp_db.get("pg_conn_refused") is None
    # Delete non-existent
    assert tmp_db.delete("does_not_exist") is False


def test_crud_update(tmp_db: FailureMemoryDB, sig_pg: FailureSignature) -> None:
    tmp_db.add(sig_pg)
    sig_pg.root_causes.append("pg_hba.conf mismatch")
    tmp_db.update(sig_pg)
    stored = tmp_db.get("pg_conn_refused")
    assert "pg_hba.conf mismatch" in stored.root_causes
