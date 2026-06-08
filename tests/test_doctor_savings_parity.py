"""Regression: doctor savings must equal status savings (honesty invariant).

The ``doctor`` command's token-savings check and the ``status`` command both read
the same ``monitor.db``. Historically ``doctor`` computed savings with a raw
``SUM(input_tokens - compressed_tokens)`` over the last 100 rows and *no*
attribution filter, while ``status`` routed through an attribution-aware engine
that credits only proxy-caused savings. The two surfaces therefore disagreed and
``doctor`` over-claimed.

These tests pin the fix:

1. The figure ``doctor`` prints equals the figure the status savings engine
   returns for the same window (parity / "single source of truth").
2. The savings denominator honors ``cache_origin``: client-placed (passthrough)
   cache reads are NOT credited as savings, so proxy-origin and client-origin
   traffic are never conflated.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

import pytest

# Schema columns that the attribution-aware savings engine reads.
_SCHEMA = """
CREATE TABLE requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost REAL,
    protected_tokens INTEGER,
    compressed_tokens INTEGER,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_origin TEXT
)
"""


def _now_local_iso() -> str:
    """A timestamp inside today's local calendar day (matches the 'today' window)."""
    # Stored timestamps are UTC; the 'today' window compares localtime, so a
    # mid-day UTC stamp is safely inside the user's local day for this test.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _seed_db(path: str, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.executemany(
        "INSERT INTO requests "
        "(timestamp, model, input_tokens, output_tokens, compressed_tokens, "
        " cache_read_tokens, cache_creation_tokens, cache_origin) "
        "VALUES (:timestamp, :model, :input_tokens, :output_tokens, "
        ":compressed_tokens, :cache_read_tokens, :cache_creation_tokens, "
        ":cache_origin)",
        rows,
    )
    conn.commit()
    conn.close()


def _mixed_rows() -> list[dict]:
    """Rows mixing proxy-origin and client-origin cache + proxy compression."""
    ts = _now_local_iso()
    return [
        # Proxy-managed cache + proxy compression → counts as tokenpak savings.
        {
            "timestamp": ts,
            "model": "claude-haiku-4-5",
            "input_tokens": 100_000,
            "output_tokens": 10_000,
            "compressed_tokens": 40_000,
            "cache_read_tokens": 50_000,
            "cache_creation_tokens": 0,
            "cache_origin": "proxy",
        },
        # Client-managed cache (passthrough) → must NOT be credited to tokenpak.
        {
            "timestamp": ts,
            "model": "claude-sonnet-4-6",
            "input_tokens": 200_000,
            "output_tokens": 20_000,
            "compressed_tokens": 80_000,  # passthrough: not proxy-caused
            "cache_read_tokens": 120_000,
            "cache_creation_tokens": 0,
            "cache_origin": "client",
        },
    ]


def _run_doctor_json(db_path: str) -> dict:
    """Run run_doctor in JSON mode against db_path; return the parsed output."""
    from tokenpak.cli.commands import doctor as doctor_mod

    captured = StringIO()
    with patch.dict("os.environ", {"TOKENPAK_DB": db_path}), \
            patch("sys.stdout", captured), \
            patch.object(doctor_mod, "_proxy_get", return_value=None):
        doctor_mod.run_doctor(output_json=True)
    return json.loads(captured.getvalue())


def _doctor_token_savings_check(out: dict) -> dict:
    for check in out["checks"]:
        if check["check"] == "token_savings":
            return check
    raise AssertionError("token_savings check missing from doctor output")


# ---------------------------------------------------------------------------
# Test 1: doctor savings == status savings (parity / single source of truth)
# ---------------------------------------------------------------------------

def test_doctor_savings_equals_status_savings(tmp_path):
    db = str(tmp_path / "monitor.db")
    _seed_db(db, _mixed_rows())

    from tokenpak.cli.commands.status import _calculate_fleet_savings

    # The status surface for the default 'today' window.
    status_report = _calculate_fleet_savings(db_path=db, period="today")
    assert "error" not in status_report
    status_saved = status_report["totals"]["saved"]
    status_pct = status_report["totals"]["savings_pct"]

    out = _run_doctor_json(db)
    check = _doctor_token_savings_check(out)
    assert check["status"] == "pass"

    # Doctor must print the SAME dollar-saved figure status computes.
    assert f"${status_saved:.4f} saved" in check["message"], (
        f"doctor message {check['message']!r} does not carry the status "
        f"saved figure ${status_saved:.4f}"
    )
    # And the SAME percentage.
    assert f"({status_pct:.1f}%)" in check["message"]
    # Detail line carries the same saved figure for machine parity.
    assert f"saved=${status_saved:.4f}" in check["detail"]


# ---------------------------------------------------------------------------
# Test 2: denominator honors cache_origin (proxy vs client not conflated)
# ---------------------------------------------------------------------------

def test_savings_denominator_honors_cache_origin(tmp_path):
    """Client-origin cache must not inflate the savings the doctor reports.

    Two DBs with identical token volumes differ only in cache_origin. The
    proxy-origin DB should report strictly more tokenpak savings than the
    client-origin DB (which credits nothing to tokenpak), proving the figure
    is attribution-aware rather than a 100%-denominator delta.
    """
    base = {
        "timestamp": _now_local_iso(),
        "model": "claude-haiku-4-5",
        "input_tokens": 100_000,
        "output_tokens": 10_000,
        "compressed_tokens": 40_000,
        "cache_read_tokens": 60_000,
        "cache_creation_tokens": 0,
    }

    proxy_db = str(tmp_path / "proxy.db")
    client_db = str(tmp_path / "client.db")
    _seed_db(proxy_db, [{**base, "cache_origin": "proxy"}])
    _seed_db(client_db, [{**base, "cache_origin": "client"}])

    from tokenpak.cli.commands.status import _calculate_fleet_savings

    proxy_saved = _calculate_fleet_savings(db_path=proxy_db, period="today")["totals"]["saved"]
    client_saved = _calculate_fleet_savings(db_path=client_db, period="today")["totals"]["saved"]

    # Proxy-caused traffic yields real tokenpak savings; identical client-origin
    # traffic must not be credited the same way (no conflation).
    assert proxy_saved > client_saved
    assert client_saved == pytest.approx(0.0, abs=1e-9)

    # And the doctor surface reflects the attribution: the proxy DB's doctor
    # figure is the proxy-origin (non-zero) one, not the conflated total.
    out = _run_doctor_json(proxy_db)
    check = _doctor_token_savings_check(out)
    assert f"${proxy_saved:.4f} saved" in check["message"]


# ---------------------------------------------------------------------------
# Test 3: no-data window degrades gracefully (no over-claim, no crash)
# ---------------------------------------------------------------------------

def test_doctor_savings_no_data_is_warn_not_overclaim(tmp_path):
    db = str(tmp_path / "monitor.db")
    _seed_db(db, [])  # empty requests table

    out = _run_doctor_json(db)
    check = _doctor_token_savings_check(out)
    assert check["status"] == "warn"
    # Must not fabricate a savings figure when there's no data.
    assert "saved" not in check["message"] or "no request data" in check["message"]
