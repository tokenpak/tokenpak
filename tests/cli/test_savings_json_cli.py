# SPDX-License-Identifier: Apache-2.0
"""`tokenpak savings --json` must emit a machine-readable document.

Sibling reporting commands (`recommendations`, `status`, `report`) accept
`--json`; `savings` previously rejected the flag at the parser (exit 2).
These tests pin the aligned contract:

* `--json` with recorded traffic emits one well-formed JSON document.
* `--json` on a fresh install emits a well-formed empty-state document and
  exits 0 — absence of data is not an error.
* The human-readable output is unchanged when the flag is absent.
* `--help` advertises the flag.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_savings(home: Path, *argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("TOKENPAK_HOME", None)
    env.pop("TOKENPAK_DB", None)
    env.pop("TOKENPAK_MONITOR_DB", None)
    env["TOKENPAK_PORT"] = "8899"  # nothing listens here
    fake_deps = home / "fake-deps"
    if fake_deps.exists():
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{fake_deps}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else str(fake_deps)
        )
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "savings", *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=90,
    )


def _install_fake_tiktoken(home: Path) -> None:
    """Provide a deterministic tokenizer module to the subprocess."""
    module_dir = home / "fake-deps"
    module_dir.mkdir()
    (module_dir / "tiktoken.py").write_text(
        "class Encoder:\n"
        "    def encode(self, text):\n"
        "        return list(range(max(1, len(text) // 5)))\n"
        "def get_encoding(name):\n"
        "    assert name == 'cl100k_base'\n"
        "    return Encoder()\n",
        encoding="utf-8",
    )


def _install_unavailable_tiktoken(home: Path, failure_mode: str) -> None:
    """Exercise dependency failures independently of the installed tokenizer."""
    module_dir = home / "fake-deps"
    module_dir.mkdir()
    if failure_mode == "missing":
        source = "raise ModuleNotFoundError('private-tokenizer-detail')\n"
    else:
        assert failure_mode == "initialization"
        source = (
            "def get_encoding(name):\n"
            "    assert name == 'cl100k_base'\n"
            "    raise OSError('private-tokenizer-detail')\n"
        )
    (module_dir / "tiktoken.py").write_text(source, encoding="utf-8")


def _fresh_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".tpk").mkdir(parents=True)
    (home / ".tpk" / ".seen_intro").touch()  # suppress the first-run banner
    return home


def _seed_monitor_db(home: Path) -> None:
    """Create a monitor store with traffic that produces nonzero savings."""
    conn = sqlite3.connect(home / ".tpk" / "monitor.db")
    conn.execute(
        "CREATE TABLE requests ("
        "timestamp TEXT, model TEXT, status_code INT, estimated_cost REAL, "
        "input_tokens INT, output_tokens INT, cache_read_tokens INT, "
        "cache_creation_tokens INT, compressed_tokens INT, protected_tokens INT)"
    )
    now = datetime.now().isoformat()
    rows = [
        (now, "test-model", 200, 0.25, 1_000, 500, 2_000, 100, 300, 0),
        (now, "test-model", 200, 0.10, 400, 200, 800, 0, 150, 0),
        (now, "test-model", 500, 9.99, 9_999, 9_999, 0, 0, 0, 0),  # errored: excluded
    ]
    conn.executemany(
        "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# --json with recorded data
# ---------------------------------------------------------------------------


def test_json_with_data_emits_wellformed_document(tmp_path):
    home = _fresh_home(tmp_path)
    _seed_monitor_db(home)

    result = _run_savings(home, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["section"] == "savings"
    assert payload["days"] == 30
    assert payload["actual_cost"] > 0
    for key in (
        "savings_amount",
        "savings_pct",
        "cache_hit_rate",
        "estimated_without_compression",
    ):
        assert key in payload, f"missing key: {key}"


def test_json_respects_days_flag(tmp_path):
    home = _fresh_home(tmp_path)
    _seed_monitor_db(home)

    result = _run_savings(home, "--json", "--days", "7")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["days"] == 7


def test_json_verify_emits_both_counts_and_divergence(tmp_path):
    home = _fresh_home(tmp_path)
    _seed_monitor_db(home)
    _install_fake_tiktoken(home)

    result = _run_savings(home, "--json", "--verify")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    verification = payload["verification"]
    assert verification["available"] is True
    assert verification["scope"] == "packaged-fixture-corpus"
    assert verification["reported_count"] > 0
    assert verification["independent_count"] > 0
    assert verification["absolute_divergence"] >= 0
    assert verification["relative_divergence"] >= 0


# ---------------------------------------------------------------------------
# --json empty state (fresh install, no stores)
# ---------------------------------------------------------------------------


def test_json_empty_state_is_wellformed_and_exits_zero(tmp_path):
    home = _fresh_home(tmp_path)

    result = _run_savings(home, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["section"] == "savings"
    assert payload["days"] == 30
    assert payload["savings_amount"] == 0.0
    assert payload["observations"] == 0
    # No readable store on a fresh install: the document says so explicitly
    # instead of passing off zeros as measurements.
    assert payload["available"] is False


# ---------------------------------------------------------------------------
# human output unchanged without the flag
# ---------------------------------------------------------------------------


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def test_human_empty_state_output_unchanged(tmp_path):
    home = _fresh_home(tmp_path)

    result = _run_savings(home)

    assert result.returncode == 0, result.stderr
    assert "No savings data yet" in result.stdout
    assert not _is_json(result.stdout), "human-readable output must not be a JSON document"


def test_human_output_with_data_unchanged(tmp_path):
    home = _fresh_home(tmp_path)
    _seed_monitor_db(home)

    result = _run_savings(home)

    assert result.returncode == 0, result.stderr
    assert "Actual Cost" in result.stdout
    assert "Est. Savings" in result.stdout


def test_human_verify_prints_scope_counts_and_divergence(tmp_path):
    home = _fresh_home(tmp_path)
    _seed_monitor_db(home)
    _install_fake_tiktoken(home)

    result = _run_savings(home, "--verify")

    assert result.returncode == 0, result.stderr
    assert "Independent token-count verification" in result.stdout
    assert "does not recount logged requests" in result.stdout
    assert "Reported (utf8-bytes-div-4)" in result.stdout
    assert "Independent (tiktoken:cl100k_base)" in result.stdout
    assert "Divergence:" in result.stdout


@pytest.mark.parametrize("failure_mode", ["missing", "initialization"])
@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("has_data", [False, True])
def test_verify_unavailable_preserves_savings_output(
    tmp_path, failure_mode: str, as_json: bool, has_data: bool
):
    home = _fresh_home(tmp_path)
    if has_data:
        _seed_monitor_db(home)
    _install_unavailable_tiktoken(home, failure_mode)
    output_args = ("--json",) if as_json else ()

    baseline = _run_savings(home, *output_args)
    result = _run_savings(home, *output_args, "--verify")

    assert baseline.returncode == 0, baseline.stderr
    assert result.returncode == 0, result.stderr
    assert "private-tokenizer-detail" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    if as_json:
        payload = json.loads(result.stdout)
        verification = payload.pop("verification")
        assert payload == json.loads(baseline.stdout)
        assert verification["available"] is False
        assert verification["scope"] == "packaged-fixture-corpus"
        assert "does not recount logged requests" in verification["scope_note"]
        for field in (
            "reported_count",
            "independent_count",
            "absolute_divergence",
            "relative_divergence",
        ):
            assert field not in verification
        message = verification["message"]
    else:
        assert result.stdout.startswith(baseline.stdout)
        assert "Independent token-count verification" in result.stdout
        assert "does not recount logged requests" in result.stdout
        assert "Unavailable:" in result.stdout
        assert "Divergence:" not in result.stdout
        message = result.stdout
    assert "tokenpak[tokens]" in message
    if failure_mode == "missing":
        assert "requires tiktoken" in message
    else:
        assert "could not initialize cl100k_base" in message
        assert "readable cache" in message


# ---------------------------------------------------------------------------
# help surface
# ---------------------------------------------------------------------------


def test_help_advertises_json_flag(tmp_path):
    home = _fresh_home(tmp_path)

    result = _run_savings(home, "--help")

    assert result.returncode == 0, result.stderr
    assert "--json" in result.stdout
    assert "--verify" in result.stdout
