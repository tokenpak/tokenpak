# SPDX-License-Identifier: Apache-2.0
"""`tokenpak cost` must distinguish three states, not two.

There is a difference between "we have no measurements", "we measured and you
spent nothing in this period", and "you spent this much". Collapsing the first
into the second told a user with no request store that we had measured their
spend and it was zero — the same absent-rendered-as-measured defect the
measured-data contract exists to remove, on the command whose whole subject is
a number.

These assert the rendered values, not the shape of the output.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cost(home: Path) -> str:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("TOKENPAK_HOME", None)
    env.pop("TOKENPAK_DB", None)
    env["TOKENPAK_PORT"] = "8899"  # nothing listens here
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "cost"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=90,
    )
    return result.stdout + result.stderr


def _make_store(home: Path, rows: list[tuple] | None = None) -> None:
    tpk = home / ".tpk"
    tpk.mkdir(parents=True, exist_ok=True)
    (tpk / ".seen_intro").touch()  # suppress the first-run banner
    conn = sqlite3.connect(tpk / "monitor.db")
    conn.execute(
        "CREATE TABLE requests (timestamp TEXT, model TEXT, input_tokens INT, "
        "output_tokens INT, estimated_cost REAL)"
    )
    for row in rows or []:
        conn.execute("INSERT INTO requests VALUES (?,?,?,?,?)", row)
    conn.commit()
    conn.close()


def _spent_line(text: str) -> str:
    for line in text.splitlines():
        if "Spent:" in line:
            return line
    raise AssertionError(f"no Spent line in output:\n{text}")


def test_no_request_store_reports_no_measurement_not_zero(tmp_path):
    home = tmp_path / "home"
    (home / ".tpk").mkdir(parents=True)
    (home / ".tpk" / ".seen_intro").touch()

    line = _spent_line(_run_cost(home))

    assert "no measurements yet" in line
    assert "$0.0000" not in line, "an absent store must not render as a measured zero"


def test_empty_store_reports_a_measured_zero(tmp_path):
    """A store that exists and records nothing IS a measurement of zero."""
    home = tmp_path / "home"
    _make_store(home)

    line = _spent_line(_run_cost(home))

    assert "$0.0000" in line
    assert "measured" in line, "a genuine zero must say it was measured"


def test_recorded_spend_is_reported_exactly(tmp_path):
    import datetime

    home = tmp_path / "home"
    today = datetime.date.today().isoformat()
    _make_store(home, [(today, "claude-opus-5", 1000, 500, 0.0234)])

    line = _spent_line(_run_cost(home))

    assert "$0.0234" in line, f"expected the recorded value, got: {line}"


def test_two_rows_sum_rather_than_report_the_last(tmp_path):
    import datetime

    home = tmp_path / "home"
    today = datetime.date.today().isoformat()
    _make_store(
        home,
        [
            (today, "claude-opus-5", 1000, 500, 0.0200),
            (today, "claude-sonnet-5", 2000, 800, 0.0100),
        ],
    )

    line = _spent_line(_run_cost(home))

    assert "$0.0300" in line, f"expected the sum of both rows, got: {line}"


@pytest.mark.parametrize("state", ["absent", "empty"])
def test_no_data_states_never_claim_savings(tmp_path, state):
    home = tmp_path / "home"
    if state == "absent":
        (home / ".tpk").mkdir(parents=True)
        (home / ".tpk" / ".seen_intro").touch()
    else:
        _make_store(home)

    out = _run_cost(home)

    # Nothing in a no-traffic state may present a savings figure.
    assert "Cost saved" not in out
    assert "Tokens saved" not in out
