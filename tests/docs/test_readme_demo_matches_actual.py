# SPDX-License-Identifier: Apache-2.0
"""The README's `tokenpak demo` block must be what `tokenpak demo` prints.

README showed a panel with a `Fewer tokens` row and an `(illustrative)` header;
the command printed `Fixture delta … (32.8%)`, a `Data source` row, a
`Fixture cost delta` row and a `Receipt status` row, under a plain header. A
reader comparing the two would conclude one of them was lying, and on a tool
whose whole claim is "we show you real numbers" that is the worst possible
first impression.

Copying the current output into README fixes today. This test fixes the drift:
the block is compared to a live run, so the next change to the panel fails
here instead of on a reader's screen.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"

# The fenced block that opens with the panel's top-left corner.
_PANEL_BLOCK = re.compile(r"```\n(┌[^`]*?┘)\n```", re.DOTALL)


def _readme_panel() -> str:
    match = _PANEL_BLOCK.search(README.read_text(encoding="utf-8"))
    assert match, "README no longer contains a fenced demo panel block"
    return match.group(1).rstrip()


def _actual_panel(tmp_home: Path) -> str:
    env = {
        "HOME": str(tmp_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "demo"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"`tokenpak demo` exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    lines = result.stdout.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("┌")), None)
    end = next((i for i, ln in enumerate(lines) if ln.startswith("└")), None)
    assert start is not None and end is not None, (
        f"`tokenpak demo` printed no panel:\n{result.stdout}"
    )
    return "\n".join(lines[start : end + 1]).rstrip()


@pytest.fixture()
def clean_home(tmp_path: Path) -> Path:
    (tmp_path / ".tpk").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tpk" / ".seen_intro").touch()
    return tmp_path


def test_readme_demo_panel_matches_command_output(clean_home: Path) -> None:
    documented = _readme_panel()
    actual = _actual_panel(clean_home)
    assert documented == actual, (
        "README's demo panel no longer matches `tokenpak demo`.\n\n"
        f"--- README ---\n{documented}\n\n--- actual ---\n{actual}\n"
    )


def test_demo_panel_states_it_is_not_a_receipt(clean_home: Path) -> None:
    """The fixture must never read as a measured saving.

    This is the semantic half: matching README is worthless if both say
    something untrue. The panel carries its own disclaimer so a screenshot
    pasted anywhere still says what it is.
    """
    actual = _actual_panel(clean_home)
    assert "Fixture" in actual, f"panel should mark its numbers as fixture data:\n{actual}"
    assert "not a savings receipt" in actual, (
        f"panel must state it is not a measured receipt:\n{actual}"
    )


def test_demo_next_steps_do_not_claim_zero_config(clean_home: Path) -> None:
    """`tokenpak serve … (zero-config)` contradicted setup, doctor and status."""
    env = {
        "HOME": str(clean_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", "demo"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    assert "zero-config" not in result.stdout, (
        f"demo still advertises a zero-config path:\n{result.stdout}"
    )
    assert "tokenpak setup" in result.stdout, (
        f"demo should point at setup as the first real step:\n{result.stdout}"
    )
