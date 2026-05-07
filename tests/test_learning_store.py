"""Tests for tokenpak.agentic.learning — Agent Learning Store.

Covers:
  - Empty store initialisation
  - model performance extraction from routing_ledger
  - compression mode extraction from calibration.json
  - block utility extraction from utility.json
  - context gap extraction from gaps.json
  - learn() full integration
  - get_best_model()
  - get_effective_compression()
  - reset()
  - load()
  - cmd_learn_status() (smoke test — no crash)
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.agentic.learning", reason="module not available in current build")
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.agentic.learning import (
    _empty_store,
    _extract_block_utility,
    _extract_compression_modes,
    _extract_context_gaps,
    _extract_model_performance,
    _load,
    _save,
    cmd_learn_status,
    get_best_model,
    get_effective_compression,
    learn,
    load,
    reset,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp(tmp_path):
    """Return a temp dir."""
    return tmp_path


def _make_ledger(tmp: Path, rows: list[dict]) -> str:
    """Create a routing_ledger.db with given transaction rows."""
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
                (timestamp, model_used, task_type, accepted)
            VALUES (?, ?, ?, ?)
        """, (
            r.get("timestamp", datetime.now(timezone.utc).isoformat()),
            r["model"],
            r.get("task_type", "UNKNOWN"),
            r.get("accepted"),
        ))
    conn.commit()
    conn.close()
    return db


def _make_calibration(tmp: Path, events: list[dict], overrides: dict | None = None) -> str:
    path = str(tmp / "calibration.json")
    data = {
        "overrides": overrides or {},
        "events": events,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    Path(path).write_text(json.dumps(data))
    return path


def _make_utility(tmp: Path, scores: dict) -> str:
    path = str(tmp / "utility.json")
    Path(path).write_text(json.dumps(scores))
    return path


def _make_gaps(tmp: Path, gaps: list[dict]) -> str:
    path = str(tmp / "gaps.json")
    Path(path).write_text(json.dumps(gaps))
    return path


def _make_learning(tmp: Path) -> str:
    return str(tmp / "learning.json")


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------

def test_empty_store_structure():
    store = _empty_store()
    assert store["version"] == 1
    assert "model_performance" in store
    assert "compression_modes" in store
    assert "block_utility" in store
    assert "context_gaps" in store
    assert store["context_gaps"]["total"] == 0


def test_load_missing_returns_empty(tmp):
    path = str(tmp / "does_not_exist.json")
    store = _load(path)
    assert store["version"] == 1
    assert store["model_performance"] == {}


# ---------------------------------------------------------------------------
# Model performance extraction
# ---------------------------------------------------------------------------

def test_extract_model_performance_basic(tmp):
    db = _make_ledger(tmp, [
        {"model": "gpt-4o", "task_type": "CODING", "accepted": 1},
        {"model": "gpt-4o", "task_type": "CODING", "accepted": 1},
        {"model": "gpt-4o", "task_type": "CODING", "accepted": 0},
        {"model": "claude-sonnet", "task_type": "CODING", "accepted": 1},
    ])
    store = _empty_store()
    perf = _extract_model_performance(db, store)

    assert "CODING" in perf
    assert "gpt-4o" in perf["CODING"]
    assert perf["CODING"]["gpt-4o"]["samples"] == 3
    assert abs(perf["CODING"]["gpt-4o"]["acceptance_rate"] - 2 / 3) < 0.001
    assert perf["CODING"]["claude-sonnet"]["acceptance_rate"] == 1.0


def test_extract_model_performance_no_db(tmp):
    store = _empty_store()
    result = _extract_model_performance(str(tmp / "missing.db"), store)
    assert result == {}


def test_extract_model_performance_unreviewed_excluded(tmp):
    """Rows with accepted=NULL should not appear in stats."""
    db = _make_ledger(tmp, [
        {"model": "gpt-4o", "task_type": "QA", "accepted": None},
        {"model": "gpt-4o", "task_type": "QA", "accepted": None},
    ])
    store = _empty_store()
    perf = _extract_model_performance(db, store)
    # No rows with accepted IS NOT NULL → empty
    assert perf.get("QA", {}) == {}


def test_extract_model_performance_multiple_task_types(tmp):
    db = _make_ledger(tmp, [
        {"model": "m1", "task_type": "CODING", "accepted": 1},
        {"model": "m1", "task_type": "REASONING", "accepted": 0},
        {"model": "m2", "task_type": "REASONING", "accepted": 1},
    ])
    store = _empty_store()
    perf = _extract_model_performance(db, store)
    assert "CODING" in perf
    assert "REASONING" in perf
    assert perf["CODING"]["m1"]["acceptance_rate"] == 1.0
    assert perf["REASONING"]["m1"]["acceptance_rate"] == 0.0
    assert perf["REASONING"]["m2"]["acceptance_rate"] == 1.0


# ---------------------------------------------------------------------------
# Compression mode extraction
# ---------------------------------------------------------------------------

def test_extract_compression_modes_basic(tmp):
    events = [
        {"type": "retry", "mode": "aggressive", "risk_classes": ["CODE"], "timestamp": datetime.now(timezone.utc).isoformat()},
        {"type": "retry", "mode": "aggressive", "risk_classes": ["CODE"], "timestamp": datetime.now(timezone.utc).isoformat()},
        {"type": "success", "mode": "aggressive", "risk_classes": [], "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    calib = _make_calibration(tmp, events)
    store = _empty_store()
    result = _extract_compression_modes(calib, store)

    assert "CODE" in result
    assert "aggressive" in result["CODE"]
    assert result["CODE"]["aggressive"]["retries"] == 2


def test_extract_compression_modes_no_file(tmp):
    store = _empty_store()
    result = _extract_compression_modes(str(tmp / "missing.json"), store)
    assert result == {}


def test_extract_compression_overrides_included(tmp):
    overrides = {"CODE": "strict"}
    calib = _make_calibration(tmp, [], overrides=overrides)
    store = _empty_store()
    result = _extract_compression_modes(calib, store)
    assert result.get("_overrides") == overrides


# ---------------------------------------------------------------------------
# Block utility extraction
# ---------------------------------------------------------------------------

def test_extract_block_utility_basic(tmp):
    scores = {
        "src/auth.py::10-50": {"score": 8.5, "hits": 5, "misses": 1, "last_cited": None},
        "src/db.py::1-30": {"score": 2.0, "hits": 0, "misses": 10, "last_cited": None},
    }
    util = _make_utility(tmp, scores)
    store = _empty_store()
    result = _extract_block_utility(util, store)
    assert "src/auth.py::10-50" in result
    assert result["src/auth.py::10-50"]["score"] == 8.5


def test_extract_block_utility_no_file(tmp):
    store = _empty_store()
    result = _extract_block_utility(str(tmp / "missing.json"), store)
    assert result == {}


# ---------------------------------------------------------------------------
# Context gap extraction
# ---------------------------------------------------------------------------

def test_extract_context_gaps_basic(tmp):
    gaps = [
        {"query": "what is auth?", "signal_type": "EXPLICIT_ASK", "evidence": "I don't have", "timestamp": datetime.now(timezone.utc).isoformat()},
        {"query": "how does db work?", "signal_type": "HALLUCINATED_IMPORT", "evidence": "import mymod", "timestamp": datetime.now(timezone.utc).isoformat()},
        {"query": "what is auth?", "signal_type": "UNCERTAIN_ANSWER", "evidence": "I think", "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    gaps_path = _make_gaps(tmp, gaps)
    store = _empty_store()
    result = _extract_context_gaps(gaps_path, store)

    assert result["total"] == 3
    assert result["by_signal"]["EXPLICIT_ASK"] == 1
    assert result["by_signal"]["HALLUCINATED_IMPORT"] == 1
    assert result["expansion_triggers"] == 2  # EXPLICIT_ASK + HALLUCINATED_IMPORT


def test_extract_context_gaps_deduplication(tmp):
    """Same query should only count once for queries_with_gaps."""
    gaps = [
        {"query": "same query", "signal_type": "UNCERTAIN_ANSWER", "evidence": "I think", "timestamp": datetime.now(timezone.utc).isoformat()},
        {"query": "same query", "signal_type": "EXPLICIT_ASK", "evidence": "I don't have", "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    gaps_path = _make_gaps(tmp, gaps)
    store = _empty_store()
    result = _extract_context_gaps(gaps_path, store)
    assert result["queries_with_gaps"] == 1


def test_extract_context_gaps_no_file(tmp):
    store = _empty_store()
    result = _extract_context_gaps(str(tmp / "missing.json"), store)
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# learn() integration
# ---------------------------------------------------------------------------

def test_learn_creates_file(tmp):
    lp = _make_learning(tmp)
    result = learn(learning_path=lp)
    assert Path(lp).exists()
    assert result["version"] == 1


def test_learn_full_integration(tmp):
    db = _make_ledger(tmp, [
        {"model": "gpt-4o", "task_type": "QA", "accepted": 1},
        {"model": "gpt-4o", "task_type": "QA", "accepted": 1},
        {"model": "claude", "task_type": "QA", "accepted": 0},
    ])
    events = [
        {"type": "retry", "mode": "hybrid", "risk_classes": ["NARRATIVE"], "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    calib = _make_calibration(tmp, events)
    scores = {"block1": {"score": 7.0, "hits": 3, "misses": 1, "last_cited": None}}
    util = _make_utility(tmp, scores)
    gaps = [
        {"query": "q1", "signal_type": "EXPLICIT_ASK", "evidence": "no info", "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    gaps_path = _make_gaps(tmp, gaps)
    lp = _make_learning(tmp)

    result = learn(
        ledger_path=db,
        calibration_path=calib,
        utility_path=util,
        gaps_path=gaps_path,
        learning_path=lp,
    )

    assert result["model_performance"]["QA"]["gpt-4o"]["acceptance_rate"] > 0.5
    assert "NARRATIVE" in result["compression_modes"]
    assert "block1" in result["block_utility"]
    assert result["context_gaps"]["total"] == 1


# ---------------------------------------------------------------------------
# get_best_model()
# ---------------------------------------------------------------------------

def test_get_best_model_returns_top_model(tmp):
    db = _make_ledger(tmp, [
        *[{"model": "model-a", "task_type": "CODING", "accepted": 1} for _ in range(8)],
        *[{"model": "model-b", "task_type": "CODING", "accepted": 1} for _ in range(3)],
        *[{"model": "model-b", "task_type": "CODING", "accepted": 0} for _ in range(3)],
    ])
    lp = _make_learning(tmp)
    learn(ledger_path=db, learning_path=lp)
    best = get_best_model("CODING", learning_path=lp, min_samples=5)
    assert best == "model-a"


def test_get_best_model_min_samples(tmp):
    """Model with fewer than min_samples should not be returned."""
    db = _make_ledger(tmp, [
        {"model": "rare-model", "task_type": "CODING", "accepted": 1},
        {"model": "rare-model", "task_type": "CODING", "accepted": 1},
    ])
    lp = _make_learning(tmp)
    learn(ledger_path=db, learning_path=lp)
    best = get_best_model("CODING", learning_path=lp, min_samples=5)
    assert best is None


def test_get_best_model_no_data(tmp):
    lp = _make_learning(tmp)
    result = get_best_model("SUMMARIZATION", learning_path=lp)
    assert result is None


# ---------------------------------------------------------------------------
# get_effective_compression()
# ---------------------------------------------------------------------------

def test_get_effective_compression_no_data(tmp):
    lp = _make_learning(tmp)
    result = get_effective_compression("CODE", "hybrid", learning_path=lp)
    assert result == "hybrid"


def test_get_effective_compression_high_retry(tmp):
    """High retry rate (>20%) should step up compression one level."""
    events = [
        *[{"type": "retry", "mode": "hybrid", "risk_classes": ["CODE"], "timestamp": datetime.now(timezone.utc).isoformat()} for _ in range(7)],
        *[{"type": "success", "mode": "hybrid", "risk_classes": [], "timestamp": datetime.now(timezone.utc).isoformat()} for _ in range(3)],
    ]
    calib = _make_calibration(tmp, events)
    lp = _make_learning(tmp)
    learn(calibration_path=calib, learning_path=lp)
    result = get_effective_compression("CODE", "hybrid", learning_path=lp)
    # retry_rate = 7/(7+3)=70% > 20% → step up from hybrid → strict
    assert result == "strict"


def test_get_effective_compression_low_retry(tmp):
    """Low retry rate should keep base mode."""
    events = [
        {"type": "retry", "mode": "hybrid", "risk_classes": ["CODE"], "timestamp": datetime.now(timezone.utc).isoformat()},
        *[{"type": "success", "mode": "hybrid", "risk_classes": [], "timestamp": datetime.now(timezone.utc).isoformat()} for _ in range(9)],
    ]
    calib = _make_calibration(tmp, events)
    lp = _make_learning(tmp)
    learn(calibration_path=calib, learning_path=lp)
    result = get_effective_compression("CODE", "hybrid", learning_path=lp)
    # retry_rate = 1/10 = 10% → no change
    assert result == "hybrid"


def test_get_effective_compression_already_strict(tmp):
    """Base mode strict should always stay strict."""
    lp = _make_learning(tmp)
    result = get_effective_compression("CODE", "strict", learning_path=lp)
    assert result == "strict"


# ---------------------------------------------------------------------------
# reset() and load()
# ---------------------------------------------------------------------------

def test_reset_clears_store(tmp):
    lp = _make_learning(tmp)
    db = _make_ledger(tmp, [{"model": "x", "task_type": "QA", "accepted": 1}])
    learn(ledger_path=db, learning_path=lp)

    # Verify data was written
    store = load(lp)
    assert store["model_performance"] != {}

    # Reset and verify
    reset(lp)
    store = load(lp)
    assert store["model_performance"] == {}
    assert store["compression_modes"] == {}
    assert store["block_utility"] == {}
    assert store["context_gaps"]["total"] == 0


def test_load_returns_persisted_store(tmp):
    lp = _make_learning(tmp)
    store = _empty_store()
    store["model_performance"] = {"CODING": {"test-model": {"acceptance_rate": 0.9, "samples": 10, "wins": 9, "losses": 1}}}
    _save(store, lp)

    loaded = load(lp)
    assert loaded["model_performance"]["CODING"]["test-model"]["acceptance_rate"] == 0.9


# ---------------------------------------------------------------------------
# cmd_learn_status() smoke test
# ---------------------------------------------------------------------------

def test_cmd_learn_status_no_crash(tmp, capsys):
    """cmd_learn_status should not raise even with empty data."""
    lp = _make_learning(tmp)
    cmd_learn_status(learning_path=lp)
    out = capsys.readouterr().out
    assert "TOKENPAK" in out
    assert "Learned Patterns" in out


def test_cmd_learn_status_with_data(tmp, capsys):
    db = _make_ledger(tmp, [
        *[{"model": "gpt-4o", "task_type": "CODING", "accepted": 1} for _ in range(6)],
    ])
    lp = _make_learning(tmp)
    learn(ledger_path=db, learning_path=lp)
    cmd_learn_status(learning_path=lp)
    out = capsys.readouterr().out
    assert "CODING" in out
    assert "gpt-4o" in out
