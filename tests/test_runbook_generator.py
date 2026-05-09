"""Tests for tokenpak.agentic.runbook_generator

Coverage:
  T1 — Runbook generated from successful episode (all fields populated)
  T2 — Template fields rendered correctly in markdown output
  T3 — Duplicate detection: same error_class+task_type → update, not create
  T4 — Retrieval by error_class works
  T5 — Trigger conditions respected: one-off (prior_occurrences < 2) → no runbook
  T6 — Trigger conditions respected: failed episode → no runbook
  T7 — Trigger conditions respected: validation not passed → no runbook
  T8 — Persistence survives reload (index + markdown files on disk)
  T9 — search() returns matching runbooks
  T10 — record_outcome() updates success_count and avg_cost
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.agentic.runbook_generator", reason="module not available in current build")
from pathlib import Path

import pytest
from tokenpak.agentic.runbook_generator import (
    Episode,
    RunbookDB,
    maybe_generate,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> RunbookDB:
    return RunbookDB(
        runbooks_dir=tmp_path / "runbooks",
        index_path=tmp_path / "runbooks" / "_index.json",
    )


def _make_episode(
    *,
    prior_occurrences: int = 3,
    success: bool = True,
    validation_passed: bool = True,
    task_type: str = "proxy_restart",
    error_class: str = "port_bind_failure",
    title: str = "Fix Port Bind Failure",
) -> Episode:
    return Episode(
        task_type=task_type,
        error_class=error_class,
        title=title,
        trigger_symptoms=["Port 8080 already in use", "Address already in use error"],
        steps=["kill $(lsof -t -i:8080)", "systemctl restart tokenpak"],
        validation="curl -s http://localhost:8080/health returns 200",
        success=success,
        validation_passed=validation_passed,
        tokens_used=1200.0,
        keywords=["port", "bind", "8080"],
        prior_occurrences=prior_occurrences,
    )


# ---------------------------------------------------------------------------
# T1: Runbook generated from successful episode
# ---------------------------------------------------------------------------


def test_t1_runbook_generated_from_successful_episode(tmp_db: RunbookDB) -> None:
    ep = _make_episode()
    rb = maybe_generate(tmp_db, ep)
    assert rb is not None, "Expected a runbook to be generated"
    assert rb.title == "Fix Port Bind Failure"
    assert rb.error_class == "port_bind_failure"
    assert rb.task_type == "proxy_restart"
    assert rb.success_count == 1
    assert rb.total_count == 1
    assert rb.avg_cost_tokens == pytest.approx(1200.0, rel=0.01)
    assert len(rb.steps) == 2
    assert len(rb.trigger_symptoms) == 2


# ---------------------------------------------------------------------------
# T2: Template fields populated correctly in markdown
# ---------------------------------------------------------------------------


def test_t2_markdown_template_fields_populated(tmp_db: RunbookDB) -> None:
    ep = _make_episode()
    rb = maybe_generate(tmp_db, ep)
    assert rb is not None

    md = render_markdown(rb)
    assert "# Fix Port Bind Failure" in md
    assert "## Trigger" in md
    assert "Port 8080 already in use" in md
    assert "## Steps" in md
    assert "kill $(lsof -t -i:8080)" in md
    assert "## Validation" in md
    assert "curl -s http://localhost:8080/health" in md
    assert "## Context" in md
    assert "First seen:" in md
    assert "Success rate:" in md
    assert "Avg cost:" in md


# ---------------------------------------------------------------------------
# T3: Duplicate detection — same error_class+task_type → no duplicate created
# ---------------------------------------------------------------------------


def test_t3_duplicate_detection_no_new_entry(tmp_db: RunbookDB) -> None:
    ep = _make_episode()
    rb1 = maybe_generate(tmp_db, ep)
    assert rb1 is not None
    assert tmp_db.count() == 1

    # Second call with same error_class + task_type
    rb2 = maybe_generate(tmp_db, ep)
    assert rb2 is not None
    assert tmp_db.count() == 1, "Should not create a duplicate"
    # The returned entry is the same runbook id
    assert rb1.runbook_id == rb2.runbook_id


# ---------------------------------------------------------------------------
# T4: Retrieval by error_class works
# ---------------------------------------------------------------------------


def test_t4_retrieval_by_error_class(tmp_db: RunbookDB) -> None:
    ep = _make_episode()
    rb = maybe_generate(tmp_db, ep)
    assert rb is not None

    found = tmp_db.find_by_error_class("port_bind_failure")
    assert found is not None
    assert found.runbook_id == rb.runbook_id

    # Unknown class returns None
    assert tmp_db.find_by_error_class("made_up_class") is None


# ---------------------------------------------------------------------------
# T5: Trigger condition — one-off (prior_occurrences < MIN_OCCURRENCES) → None
# ---------------------------------------------------------------------------


def test_t5_trigger_condition_one_off_not_generated(tmp_db: RunbookDB) -> None:
    ep = _make_episode(prior_occurrences=1)
    rb = maybe_generate(tmp_db, ep)
    assert rb is None
    assert tmp_db.count() == 0


# ---------------------------------------------------------------------------
# T6: Trigger condition — failed episode → None
# ---------------------------------------------------------------------------


def test_t6_trigger_condition_failed_episode_not_generated(tmp_db: RunbookDB) -> None:
    ep = _make_episode(success=False)
    rb = maybe_generate(tmp_db, ep)
    assert rb is None
    assert tmp_db.count() == 0


# ---------------------------------------------------------------------------
# T7: Trigger condition — validation not passed → None
# ---------------------------------------------------------------------------


def test_t7_trigger_condition_validation_not_passed(tmp_db: RunbookDB) -> None:
    ep = _make_episode(validation_passed=False)
    rb = maybe_generate(tmp_db, ep)
    assert rb is None
    assert tmp_db.count() == 0


# ---------------------------------------------------------------------------
# T8: Persistence survives reload
# ---------------------------------------------------------------------------


def test_t8_persistence_survives_reload(tmp_path: Path) -> None:
    runbooks_dir = tmp_path / "runbooks"
    index_path = runbooks_dir / "_index.json"

    db1 = RunbookDB(runbooks_dir=runbooks_dir, index_path=index_path)
    ep = _make_episode()
    rb = maybe_generate(db1, ep)
    assert rb is not None

    # Markdown file should exist
    md_path = runbooks_dir / f"{rb.slug}.md"
    assert md_path.exists(), "Markdown runbook file not written"

    # Reload from disk
    db2 = RunbookDB(runbooks_dir=runbooks_dir, index_path=index_path)
    assert db2.count() == 1
    reloaded = db2.get(rb.runbook_id)
    assert reloaded is not None
    assert reloaded.title == rb.title
    assert reloaded.error_class == rb.error_class


# ---------------------------------------------------------------------------
# T9: search() returns matching runbooks
# ---------------------------------------------------------------------------


def test_t9_search_returns_matching_runbooks(tmp_db: RunbookDB) -> None:
    ep1 = _make_episode(title="Fix Port Bind Failure", error_class="port_bind_failure")
    ep2 = Episode(
        task_type="token_refresh",
        error_class="auth_error",
        title="Refresh Auth Token",
        trigger_symptoms=["401 Unauthorized response", "Token expired error in logs"],
        steps=["tokenpak refresh-token", "validate with tokenpak status"],
        validation="tokenpak status returns authenticated",
        success=True,
        validation_passed=True,
        tokens_used=800.0,
        keywords=["auth", "token", "401"],
        prior_occurrences=5,
    )
    maybe_generate(tmp_db, ep1)
    maybe_generate(tmp_db, ep2)

    results = tmp_db.search("auth")
    assert len(results) == 1
    assert results[0].error_class == "auth_error"

    results_port = tmp_db.search("port")
    assert len(results_port) == 1
    assert results_port[0].error_class == "port_bind_failure"

    # Query that matches both
    all_results = tmp_db.search("Fix")
    assert len(all_results) >= 1


# ---------------------------------------------------------------------------
# T10: record_outcome() updates success_count and rolling avg cost
# ---------------------------------------------------------------------------


def test_t10_record_outcome_updates_counts_and_cost(tmp_db: RunbookDB) -> None:
    ep = _make_episode(prior_occurrences=3)
    rb = maybe_generate(tmp_db, ep)
    assert rb is not None

    initial_success = rb.success_count
    initial_total = rb.total_count

    updated = tmp_db.record_outcome(rb.runbook_id, success=True, tokens_used=600.0)
    assert updated is not None
    assert updated.success_count == initial_success + 1
    assert updated.total_count == initial_total + 1
    # Avg cost should be a blend of 1200 and 600
    assert updated.avg_cost_tokens < 1200.0
    assert updated.avg_cost_tokens > 0.0

    # Failed outcome should increment total but not success
    total_before_second = updated.total_count
    success_before_second = updated.success_count
    updated2 = tmp_db.record_outcome(rb.runbook_id, success=False, tokens_used=300.0)
    assert updated2 is not None
    assert updated2.success_count == success_before_second
    assert updated2.total_count == total_before_second + 1
