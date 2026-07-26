# SPDX-License-Identifier: Apache-2.0
"""A config that exists but cannot be parsed is its own state.

``load_config`` returns ``{}`` both when there is no config and when the
config is unreadable, and callers could not tell the two apart. The visible
consequences, on a config whose only defect was a YAML syntax error on line 2:

  * ``tokenpak start`` validated the empty fallback dict, reported
    "Required field 'api_keys' is missing", pointed the user at their API
    key, and exited 3 — "not configured" — for a config that existed.
  * ``tokenpak status`` reported ``Config  found``.

These assert the rendered diagnosis and the exit code, not output shape.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.cli.exit_codes import EXIT_CORRUPT_STATE, EXIT_NO_DATA

REPO_ROOT = Path(__file__).resolve().parents[2]

BROKEN_YAML = "proxy:\n  port: [this is: not valid\n   yaml at all ]]]\n"


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
        "TOKENPAK_PORT": "8899",  # nothing listens here
    }
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )


@pytest.fixture()
def corrupt_home(tmp_path: Path) -> Path:
    tpk = tmp_path / ".tpk"
    tpk.mkdir(parents=True)
    (tpk / ".seen_intro").touch()
    (tpk / "config.yaml").write_text(BROKEN_YAML)
    return tmp_path


@pytest.fixture()
def empty_home(tmp_path: Path) -> Path:
    tpk = tmp_path / ".tpk"
    tpk.mkdir(parents=True)
    (tpk / ".seen_intro").touch()
    return tmp_path


def test_start_reports_the_parse_error_not_a_missing_api_key(corrupt_home: Path) -> None:
    result = _run(corrupt_home, "start")
    combined = result.stdout + result.stderr

    assert result.returncode == EXIT_CORRUPT_STATE, (
        f"corrupt config should exit {EXIT_CORRUPT_STATE} (corrupt state), "
        f"got {result.returncode}\n{combined}"
    )
    assert "could not be parsed" in combined, combined
    assert "config.yaml" in combined, combined
    assert "api_keys" not in combined, (
        "a YAML syntax error must not be reported as a missing API key:\n" + combined
    )


def test_start_on_a_genuinely_absent_config_still_says_not_configured(empty_home: Path) -> None:
    """The corrupt path must not swallow the not-configured path."""
    result = _run(empty_home, "start")
    combined = result.stdout + result.stderr

    assert result.returncode != EXIT_CORRUPT_STATE, (
        "an absent config is not corrupt state:\n" + combined
    )
    assert "could not be parsed" not in combined, combined


def test_status_reports_the_config_as_unreadable(corrupt_home: Path) -> None:
    result = _run(corrupt_home, "status")
    lines = [ln for ln in result.stdout.splitlines() if "Config" in ln]
    assert lines, f"status printed no Config row:\n{result.stdout}"
    row = lines[0]

    assert "unreadable" in row, f"status still reports a broken config as fine: {row!r}"
    assert "found" not in row.replace("not found", ""), row


def test_doctor_marks_the_corrupt_config_as_an_error(corrupt_home: Path) -> None:
    result = _run(corrupt_home, "doctor")
    config_lines = [ln for ln in result.stdout.splitlines() if "Config file" in ln]
    assert config_lines, f"doctor printed no Config file check:\n{result.stdout}"
    assert "could not be parsed" in config_lines[0], config_lines[0]
    assert "❌" in config_lines[0], (
        f"an unparseable config is an error, not a warning: {config_lines[0]!r}"
    )


def test_preview_exit_code_matches_the_state_it_reports(empty_home: Path) -> None:
    """--json said `no_data` while the exit code said generic failure."""
    result = _run(empty_home, "preview", "--json")

    assert '"state": "no_data"' in result.stdout, result.stdout
    assert result.returncode == EXIT_NO_DATA, (
        f"body reports no_data; exit code should be {EXIT_NO_DATA}, got {result.returncode}"
    )
