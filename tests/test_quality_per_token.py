"""Tests for quality_per_token metric in tokenpak.agentic.learning.

Covers:
  - _extract_quality_per_token() from routing_ledger
  - record_quality_per_token() incremental recording
  - get_best_quality_per_token() query
  - get_compression_quality_signal() optimization signal
  - learn() integration with QPT extraction
  - cmd_learn_status() includes QPT section
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.agentic.learning", reason="module not available in current build")
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.agentic.learning import (
    _empty_store,
    _extract_quality_per_token,
    _load,
    _save,
    cmd_learn_status,
    get_best_quality_per_token,
    get_compression_quality_signal,
    learn,
    record_quality_per_token,
    reset,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp(tmp_path):
    return tmp_path


def _make_ledger(tmp: Path, rows: list[dict]) -> str:
    """Create a routing_ledger.db with given rows including token counts."""
    db = str(tmp / "routing_ledger.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT,
            model_used       TEXT,
            task_type        TEXT DEFAULT 'UNKNOWN',
            complexity_score REAL DEFAULT 0.0,
            context_tokens   INTEGER DEFAULT 0,
            context_weight   REAL DEFAULT 0.0,
            response_tokens  INTEGER DEFAULT 0,
            accepted         INTEGER,
            rejection_reason TEXT,
            latency_ms       REAL DEFAULT 0.0,
            query_preview    TEXT,
            routing_action   TEXT DEFAULT 'passthrough'
        )
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO transactions
                (timestamp, model_used, task_type, accepted,
                 context_tokens, response_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            r.get("timestamp", datetime.now(timezone.utc).isoformat()),
            r.get("model", "test-model"),
            r.get("task_type", "UNKNOWN"),
            r.get("accepted"),
            r.get("context_tokens", 0),
            r.get("response_tokens", 0),
        ))
    conn.commit()
    conn.close()
    return db


def _lp(tmp: Path) -> str:
    return str(tmp / "learning.json")


# ---------------------------------------------------------------------------
# _extract_quality_per_token()
# ---------------------------------------------------------------------------


def test_extract_qpt_basic(tmp):
    """Accepted rows produce correct avg_qpt."""
    db = _make_ledger(tmp, [
        {"model": "gpt-4o", "task_type": "CODING", "accepted": 1,
         "context_tokens": 900, "response_tokens": 100},  # 1.0/1000 = 0.001
        {"model": "gpt-4o", "task_type": "CODING", "accepted": 1,
         "context_tokens": 400, "response_tokens": 100},  # 1.0/500 = 0.002
    ])
    store = _empty_store()
    result = _extract_quality_per_token(db, store, compression_mode="hybrid")

    key = "gpt-4o|hybrid|CODING"
    assert key in result
    stats = result[key]
    assert stats["samples"] == 2
    assert stats["total_tokens"] == 1500
    assert stats["total_outcome"] == 2.0
    # avg_qpt = 2.0 / 1500 ≈ 0.00133 (rounded to 8 decimal places)
    assert abs(stats["avg_qpt"] - 2.0 / 1500) < 1e-7


def test_extract_qpt_rejected_rows(tmp):
    """Rejected rows (accepted=0) contribute outcome_score=0.0."""
    db = _make_ledger(tmp, [
        {"model": "m1", "task_type": "QA", "accepted": 0,
         "context_tokens": 500, "response_tokens": 100},
    ])
    store = _empty_store()
    result = _extract_quality_per_token(db, store, compression_mode="strict")

    key = "m1|strict|QA"
    assert key in result
    assert result[key]["total_outcome"] == 0.0
    assert result[key]["avg_qpt"] == 0.0
    assert result[key]["samples"] == 1


def test_extract_qpt_zero_tokens_excluded(tmp):
    """Rows with zero tokens are excluded from QPT calculation."""
    db = _make_ledger(tmp, [
        {"model": "m1", "task_type": "QA", "accepted": 1,
         "context_tokens": 0, "response_tokens": 0},
        {"model": "m1", "task_type": "QA", "accepted": 1,
         "context_tokens": 200, "response_tokens": 50},
    ])
    store = _empty_store()
    result = _extract_quality_per_token(db, store)

    key = "m1|unknown|QA"
    assert key in result
    # Only the row with tokens > 0 should be counted
    assert result[key]["samples"] == 1
    assert result[key]["total_tokens"] == 250


def test_extract_qpt_missing_db(tmp):
    """Missing DB should return empty without crashing."""
    store = _empty_store()
    result = _extract_quality_per_token(str(tmp / "missing.db"), store)
    assert result == {}


def test_extract_qpt_multiple_models_and_modes(tmp):
    """Each (model, compression_mode, task_type) combo gets its own key."""
    db = _make_ledger(tmp, [
        {"model": "m1", "task_type": "CODING", "accepted": 1,
         "context_tokens": 1000, "response_tokens": 0},
        {"model": "m2", "task_type": "CODING", "accepted": 1,
         "context_tokens": 500, "response_tokens": 0},
    ])
    store = _empty_store()
    # Call twice with different compression_mode (simulates separate extractions)
    _extract_quality_per_token(db, store, compression_mode="aggressive")
    # The second call overwrites (full re-scan semantics)
    result = _extract_quality_per_token(db, store, compression_mode="aggressive")

    assert "m1|aggressive|CODING" in result
    assert "m2|aggressive|CODING" in result


# ---------------------------------------------------------------------------
# record_quality_per_token()
# ---------------------------------------------------------------------------


def test_record_qpt_creates_entry(tmp):
    lp = _lp(tmp)
    record_quality_per_token(
        model="claude-sonnet",
        task_type="CODING",
        outcome_score=1.0,
        tokens_used=800,
        compression_mode="hybrid",
        learning_path=lp,
    )
    store = _load(lp)
    key = "claude-sonnet|hybrid|CODING"
    assert key in store["quality_per_token"]
    assert store["quality_per_token"][key]["samples"] == 1
    assert store["quality_per_token"][key]["avg_qpt"] == pytest.approx(1.0 / 800)


def test_record_qpt_incremental_update(tmp):
    """Multiple calls accumulate correctly."""
    lp = _lp(tmp)
    record_quality_per_token("m", "QA", 1.0, 1000, "strict", lp)
    record_quality_per_token("m", "QA", 0.0, 500, "strict", lp)

    store = _load(lp)
    stats = store["quality_per_token"]["m|strict|QA"]
    assert stats["samples"] == 2
    assert stats["total_outcome"] == 1.0
    assert stats["total_tokens"] == 1500
    assert stats["avg_qpt"] == pytest.approx(1.0 / 1500, abs=1e-7)


def test_record_qpt_partial_outcome(tmp):
    """Partial outcome score (0.5) is supported."""
    lp = _lp(tmp)
    record_quality_per_token("m", "QA", 0.5, 200, learning_path=lp)
    store = _load(lp)
    stats = store["quality_per_token"]["m|unknown|QA"]
    assert stats["total_outcome"] == 0.5
    assert stats["avg_qpt"] == pytest.approx(0.5 / 200)


def test_record_qpt_zero_tokens_skipped(tmp):
    """Calls with tokens_used=0 should be silently skipped."""
    lp = _lp(tmp)
    record_quality_per_token("m", "QA", 1.0, 0, learning_path=lp)
    store = _load(lp)
    assert store["quality_per_token"] == {}


# ---------------------------------------------------------------------------
# get_best_quality_per_token()
# ---------------------------------------------------------------------------


def test_get_best_qpt_returns_highest(tmp):
    lp = _lp(tmp)
    # Model A: 1.0/500 = 0.002 QPT — winner
    for _ in range(6):
        record_quality_per_token("model-a", "CODING", 1.0, 500, "aggressive", lp)
    # Model B: 1.0/2000 = 0.0005 QPT — lower
    for _ in range(6):
        record_quality_per_token("model-b", "CODING", 1.0, 2000, "hybrid", lp)

    best = get_best_quality_per_token("CODING", learning_path=lp, min_samples=5)
    assert best is not None
    assert best["model"] == "model-a"
    assert best["compression_mode"] == "aggressive"


def test_get_best_qpt_min_samples_enforced(tmp):
    """Entry with fewer than min_samples should not be returned."""
    lp = _lp(tmp)
    record_quality_per_token("rare-model", "CODING", 1.0, 100, "hybrid", lp)
    best = get_best_quality_per_token("CODING", learning_path=lp, min_samples=5)
    assert best is None


def test_get_best_qpt_no_data(tmp):
    lp = _lp(tmp)
    best = get_best_quality_per_token("SUMMARIZATION", learning_path=lp)
    assert best is None


def test_get_best_qpt_different_task_types(tmp):
    """Results are scoped to the requested task_type."""
    lp = _lp(tmp)
    for _ in range(6):
        record_quality_per_token("coding-model", "CODING", 1.0, 100, "hybrid", lp)
        record_quality_per_token("qa-model", "QA", 1.0, 200, "hybrid", lp)

    best_coding = get_best_quality_per_token("CODING", learning_path=lp, min_samples=5)
    best_qa = get_best_quality_per_token("QA", learning_path=lp, min_samples=5)

    assert best_coding["model"] == "coding-model"
    assert best_qa["model"] == "qa-model"


# ---------------------------------------------------------------------------
# get_compression_quality_signal()
# ---------------------------------------------------------------------------


def test_compression_signal_prefer_compression(tmp):
    """When aggressive gives higher QPT than strict, prefer_compression=True."""
    lp = _lp(tmp)
    # Aggressive: 1.0/300 ≈ 0.0033 QPT
    for _ in range(6):
        record_quality_per_token("m", "CODING", 1.0, 300, "aggressive", lp)
    # Strict: 1.0/800 ≈ 0.00125 QPT
    for _ in range(6):
        record_quality_per_token("m", "CODING", 1.0, 800, "strict", lp)

    signal = get_compression_quality_signal("m", "CODING", learning_path=lp, min_samples=5)
    assert signal["prefer_compression"] is True
    assert signal["best_mode"] == "aggressive"
    assert "aggressive" in signal["recommendation"].lower()


def test_compression_signal_back_off_compression(tmp):
    """When strict gives higher QPT than aggressive, prefer_compression=False."""
    lp = _lp(tmp)
    # Aggressive: 0.0/500 = 0.0 (all failures)
    for _ in range(6):
        record_quality_per_token("m", "CODING", 0.0, 500, "aggressive", lp)
    # Strict: 1.0/600 ≈ 0.00167 QPT
    for _ in range(6):
        record_quality_per_token("m", "CODING", 1.0, 600, "strict", lp)

    signal = get_compression_quality_signal("m", "CODING", learning_path=lp, min_samples=5)
    assert signal["prefer_compression"] is False
    assert signal["best_mode"] == "strict"
    assert "strict" in signal["recommendation"].lower()


def test_compression_signal_insufficient_data(tmp):
    """Returns prefer_compression=False with 'insufficient data' when no trusted entries."""
    lp = _lp(tmp)
    record_quality_per_token("m", "CODING", 1.0, 500, "hybrid", lp)  # only 1 sample

    signal = get_compression_quality_signal("m", "CODING", learning_path=lp, min_samples=5)
    assert signal["best_mode"] is None
    assert signal["prefer_compression"] is False
    assert "insufficient" in signal["recommendation"]


def test_compression_signal_modes_dict(tmp):
    """modes dict includes all tracked modes regardless of min_samples."""
    lp = _lp(tmp)
    record_quality_per_token("m", "CODING", 1.0, 500, "hybrid", lp)
    record_quality_per_token("m", "CODING", 1.0, 400, "strict", lp)

    signal = get_compression_quality_signal("m", "CODING", learning_path=lp, min_samples=10)
    assert "hybrid" in signal["modes"]
    assert "strict" in signal["modes"]


# ---------------------------------------------------------------------------
# learn() integration
# ---------------------------------------------------------------------------


def test_learn_extracts_qpt(tmp):
    """learn() should populate quality_per_token from the routing ledger."""
    db = _make_ledger(tmp, [
        {"model": "gpt-4o", "task_type": "QA", "accepted": 1,
         "context_tokens": 800, "response_tokens": 200},
        {"model": "gpt-4o", "task_type": "QA", "accepted": 1,
         "context_tokens": 600, "response_tokens": 150},
    ])
    lp = _lp(tmp)
    result = learn(ledger_path=db, learning_path=lp)

    assert "quality_per_token" in result
    # Key uses default compression_mode="unknown"
    key = "gpt-4o|unknown|QA"
    assert key in result["quality_per_token"]
    assert result["quality_per_token"][key]["samples"] == 2


# ---------------------------------------------------------------------------
# cmd_learn_status() includes QPT section
# ---------------------------------------------------------------------------


def test_cmd_learn_status_shows_qpt(tmp, capsys):
    lp = _lp(tmp)
    for _ in range(6):
        record_quality_per_token("fast-model", "CODING", 1.0, 300, "aggressive", lp)

    cmd_learn_status(learning_path=lp)
    out = capsys.readouterr().out
    assert "Quality-per-Token" in out
    assert "fast-model" in out
    assert "CODING" in out


def test_cmd_learn_status_qpt_no_data(tmp, capsys):
    lp = _lp(tmp)
    cmd_learn_status(learning_path=lp)
    out = capsys.readouterr().out
    assert "Quality-per-Token" in out
    assert "no data yet" in out
