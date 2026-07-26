# SPDX-License-Identifier: Apache-2.0
"""Importing proxy config must not change the process environment.

`proxy/config.py` used to apply the active profile with
`os.environ.setdefault(...)` over the whole preset at import time. Any command
that transitively imported proxy config therefore gained ~8 environment
variables the user never set. Two things broke:

  * `doctor` reported `Trace mode enabled (TOKENPAK_TRACE=1)` for a variable
    TokenPak had set on itself, and
  * `doctor`'s own "env var conflicts: none detected" check was evaluated
    against the environment TokenPak had just mutated, so it could not see a
    real conflict and could invent a false one.

The profile is still a floor — explicit environment always wins — but it is
folded in as a *default* at resolution time instead of being written to the
process. These assert the resolved values and their provenance, not the shape
of any output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = """
import json, os
before = dict(os.environ)
import tokenpak.proxy.config as c
after = dict(os.environ)
print(json.dumps({
    "added": sorted(set(after) - set(before)),
    "changed": sorted(k for k in before if before[k] != after.get(k)),
    "profile": c.ACTIVE_PROFILE,
    "trace_enabled": bool(c.TRACE_ENABLED),
    "trace_origin": c.setting_origin("TOKENPAK_TRACE"),
    "mode_origin": c.setting_origin("TOKENPAK_MODE"),
    "threshold": c.__dict__.get("COMPACT_THRESHOLD_TOKENS"),
}))
"""


def _probe(home: Path, **env_overrides: str) -> dict:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # An isolated HOME is mandatory here: resolving a path in write mode
        # creates it, and probing against the live HOME has mutated a real
        # machine before.
        "TOKENPAK_HOME": str(home / ".tpk"),
    }
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, f"probe failed: {result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / ".tpk").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_import_adds_no_environment_variables(home: Path) -> None:
    out = _probe(home)
    assert out["added"] == [], (
        f"importing proxy config added {out['added']} to the environment; "
        "the profile must be a resolution-time default, not a mutation"
    )
    assert out["changed"] == [], f"importing proxy config changed {out['changed']}"


def test_profile_still_supplies_its_values(home: Path) -> None:
    """Not mutating the environment must not mean losing the profile."""
    out = _probe(home, TOKENPAK_PROFILE="balanced")
    assert out["profile"] == "balanced"
    # balanced sets TOKENPAK_TRACE=true and COMPACT_THRESHOLD_TOKENS=1500.
    assert out["trace_enabled"] is True
    assert out["trace_origin"] == "profile"
    assert out["threshold"] == 1500, (
        f"balanced profile should resolve a 1500-token threshold, got {out['threshold']}"
    )


def test_explicit_environment_beats_the_profile(home: Path) -> None:
    """'Profile is a floor' — the documented precedence must survive the change."""
    out = _probe(home, TOKENPAK_PROFILE="balanced", TOKENPAK_COMPACT_THRESHOLD_TOKENS="99")
    assert out["threshold"] == 99, "an explicit env var must override the profile"
    assert out["mode_origin"] == "profile", "unset keys still come from the profile"


def test_trace_origin_distinguishes_user_intent_from_profile(home: Path) -> None:
    profile_only = _probe(home)
    assert profile_only["trace_origin"] == "profile"

    user_set = _probe(home, TOKENPAK_TRACE="1")
    assert user_set["trace_origin"] == "env"
    assert user_set["trace_enabled"] is True

    user_off = _probe(home, TOKENPAK_TRACE="0")
    assert user_off["trace_origin"] == "env"
    assert user_off["trace_enabled"] is False, (
        "an explicit TOKENPAK_TRACE=0 must turn tracing off even though the "
        "balanced profile enables it"
    )


def test_unknown_profile_contributes_nothing(home: Path) -> None:
    out = _probe(home, TOKENPAK_PROFILE="not-a-real-profile")
    assert out["profile"] == "custom"
    assert out["trace_origin"] == "default", (
        "an unrecognised profile must not silently supply another profile's values"
    )
    assert out["added"] == []


def test_doctor_does_not_claim_the_user_enabled_trace(home: Path, tmp_path: Path) -> None:
    """The user-visible half of the fix, asserted on the rendered line."""
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "doctor"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    trace_lines = [ln for ln in result.stdout.splitlines() if "Trace mode" in ln]
    assert trace_lines, f"doctor printed no trace line:\n{result.stdout}"
    line = trace_lines[0]
    assert "TOKENPAK_TRACE=1" not in line, (
        f"doctor still claims the user set TOKENPAK_TRACE: {line!r}"
    )
    assert "profile default" in line, f"doctor should attribute trace to the profile, got: {line!r}"
