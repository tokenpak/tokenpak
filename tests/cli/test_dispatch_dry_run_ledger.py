# SPDX-License-Identifier: Apache-2.0
"""Dry-run ledger guarantee for ``tokenpak dispatch run --dry-run``.

The Dispatch guide claims ``--dry-run`` "never touches the ledger". This module
pins that contract: a dry-run must not create ``runs.db`` and, if the ledger
already exists, must not add or mutate any job / manifest / route / decision row.

Intake and route selection are pure in-memory computations, so a dry-run can still
render the full routing outcome — it just skips the persistence block entirely
(constructing the ledger alone would create ``runs.db``).
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys

import pytest

pytest.importorskip("pydantic")

import tokenpak.cli.commands.dispatch_cmd as dc  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenpak")
    sub = parser.add_subparsers(dest="command")
    dc.build_dispatch_parser(sub)
    return parser


def _invoke(argv):
    parser = _parser()
    args = parser.parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    exc = None
    rc = None
    try:
        rc = args.func(args)
    except BaseException as e:  # noqa: BLE001
        exc = e
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return rc, out.getvalue(), err.getvalue(), exc


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


def _ledger_db(home_dir):
    return home_dir / "dispatch" / "runs.db"


def _table_counts(db_path) -> dict:
    con = sqlite3.connect(str(db_path))
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# No ledger created by a cold dry-run
# ---------------------------------------------------------------------------


def test_dry_run_creates_no_ledger(home):
    rc, out, err, exc = _invoke(
        ["dispatch", "run", "add a hello function", "--dry-run", "--json"]
    )
    assert exc is None, err
    assert rc == 0
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["persisted"] is False
    # Hard guarantee: no runs.db anywhere under the home.
    assert not _ledger_db(home).exists()
    assert list(home.rglob("runs.db")) == []


def test_real_run_creates_ledger(home):
    """Contrast: a non-dry run DOES persist — so the dry-run absence is meaningful."""
    rc, out, err, exc = _invoke(["dispatch", "run", "add a hello function", "--json"])
    assert exc is None, err
    assert rc == 0
    payload = json.loads(out)
    assert payload["dry_run"] is False
    assert payload["persisted"] is True
    assert _ledger_db(home).exists()


# ---------------------------------------------------------------------------
# Dry-run against an EXISTING ledger is row-identical
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate_existing_ledger(home):
    # Seed a real run so the ledger exists with rows.
    rc, _, err, exc = _invoke(["dispatch", "run", "fix the parser bug", "--json"])
    assert exc is None and rc == 0, err
    db = _ledger_db(home)
    assert db.exists()
    before = _table_counts(db)

    # A dry-run must add/mutate nothing.
    rc, out, err, exc = _invoke(
        ["dispatch", "run", "add another feature", "--dry-run", "--json"]
    )
    assert exc is None, err
    assert rc == 0
    after = _table_counts(db)
    assert after == before, f"dry-run mutated ledger rows: {before} -> {after}"


def test_dry_run_writes_no_job_row(home):
    """Even against a fresh ledger opened by a prior real run, the dry-run job
    id must never appear as a persisted row."""
    rc, _, err, exc = _invoke(["dispatch", "run", "seed job", "--json"])
    assert exc is None and rc == 0, err

    rc, out, err, exc = _invoke(["dispatch", "run", "draft only", "--dry-run", "--json"])
    assert exc is None and rc == 0, err
    dry_job_id = json.loads(out)["job_id"]

    con = sqlite3.connect(str(_ledger_db(home)))
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM dispatch_jobs WHERE id = ?", (dry_job_id,)
        ).fetchone()
    finally:
        con.close()
    assert row[0] == 0, "dry-run job id was persisted to the ledger"


# ---------------------------------------------------------------------------
# Human output signals the draft-only semantics
# ---------------------------------------------------------------------------


def test_dry_run_human_output_notes_no_persist(home):
    rc, out, err, exc = _invoke(["dispatch", "run", "fix a bug", "--dry-run"])
    assert exc is None, err
    assert rc == 0
    assert "dry-run" in out.lower()
    assert "ledger" in out.lower()
    assert not _ledger_db(home).exists()
