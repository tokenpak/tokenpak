# SPDX-License-Identifier: Apache-2.0
"""Reading telemetry must not create state, and must not read someone else's.

Two defects with one visible symptom — `tokenpak leaderboard` on a fresh
install printing a raw `sqlite3.OperationalError: no such table: tp_events`
traceback:

1. ``_get_conn`` used ``sqlite3.connect``, which *creates* the file when it is
   absent. A read materialised an empty ``telemetry.db`` at the ambient umask
   and then failed on the first query. Read paths must not create state.

2. ``get_db_path`` preferred a database sitting in the source tree over the
   user's own home. On a source checkout that silently shadowed the real
   store: an isolated HOME with no telemetry.db still answered from a stale
   repo-root file the user never created and could not see.

"Nothing recorded yet" is a normal condition with a defined representation.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = """
import json, os
from pathlib import Path
from tokenpak.telemetry import query_dsl as q

resolved = q._default_db_path()
out = {"resolved": str(resolved), "existed_before": resolved.exists()}
try:
    q._get_conn()
    out["raised"] = None
except q.TelemetryUnavailable as exc:
    out["raised"] = "TelemetryUnavailable"
except Exception as exc:
    out["raised"] = type(exc).__name__
out["created"] = resolved.exists() and not out["existed_before"]
out["usage"] = q.get_model_usage()
out["events"] = q.get_recent_events()
report = q.get_savings_report()
out["savings_available"] = report.available
out["savings_observations"] = report.observations
print(json.dumps(out))
"""


def _probe(home: Path, cwd: Path) -> dict:
    import json

    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        timeout=180,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / ".tpk").mkdir(parents=True)
    return tmp_path


def test_reading_an_absent_store_creates_nothing(home: Path) -> None:
    out = _probe(home, REPO_ROOT)

    assert out["raised"] == "TelemetryUnavailable", (
        f"expected the typed unavailable signal, got {out['raised']}"
    )
    assert not out["created"], f"a read created {out['resolved']}"
    assert not (home / ".tpk" / "telemetry.db").exists()


def test_readers_degrade_instead_of_raising(home: Path) -> None:
    """The symptom users actually hit: a traceback out of a query helper."""
    out = _probe(home, REPO_ROOT)

    assert out["usage"] == []
    assert out["events"] == []
    # available=False is "could not read the store", distinct from a healthy
    # store with zero observations. It must never be rendered as measured 0.
    assert out["savings_available"] is False
    assert out["savings_observations"] == 0


def test_a_database_in_the_source_tree_does_not_shadow_the_home(home: Path, tmp_path: Path) -> None:
    """Resolution must answer to HOME, not to whatever is next to the code."""
    fake_repo = tmp_path / "checkout"
    fake_repo.mkdir()
    decoy = fake_repo / "telemetry.db"
    conn = sqlite3.connect(decoy)
    conn.execute("CREATE TABLE tp_pricing (x INT)")
    conn.commit()
    conn.close()

    out = _probe(home, REPO_ROOT)
    assert str(decoy) != out["resolved"]
    assert str(home) in out["resolved"], (
        f"resolved to {out['resolved']}, which is outside the user's home"
    )


@pytest.mark.parametrize("verb", ["leaderboard", "compare"])
def test_telemetry_verbs_report_no_data_instead_of_a_traceback(home: Path, verb: str) -> None:
    (home / ".tpk" / ".seen_intro").touch()
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", verb],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            "HOME": str(home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "TOKENPAK_PORT": "8899",
        },
        timeout=180,
    )
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, f"`{verb}` still crashes:\n{combined}"
    assert "no such table" not in combined, combined
    assert result.returncode == 0, f"`{verb}` exited {result.returncode}:\n{combined}"
