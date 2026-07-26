# SPDX-License-Identifier: Apache-2.0
"""The beta allowlist must stay paired with the live parser.

These tests are the mechanism that makes the allowlist real. Without them it
is a document; with them, a verb cannot enter or leave the product without a
deliberate classification, and a command cannot be advertised unless it
actually works.

Everything here derives from the live parser rather than a hardcoded roster —
the defect being prevented is precisely a hardcoded list drifting away from
what ships.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak._cli_core import build_parser
from tokenpak.core.registry import beta_surface

REPO_ROOT = Path(__file__).resolve().parent.parent


def live_commands() -> set[str]:
    """Every subcommand the shipped parser exposes."""
    parser = build_parser()
    names: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names |= set(action.choices)
    return names


LIVE = sorted(live_commands())
SUPPORTED = sorted(beta_surface.supported_commands())


# ---------------------------------------------------------------------------
# The allowlist covers the parser, exactly
# ---------------------------------------------------------------------------


def test_every_live_command_is_classified():
    """A new verb must be classified before it can ship."""
    unclassified = [c for c in LIVE if beta_surface.classify(c) == beta_surface.UNCLASSIFIED]
    assert not unclassified, (
        "These commands are reachable but absent from the beta surface manifest. "
        "Add each to 'supported' or to 'excluded' with a reason: " + ", ".join(unclassified)
    )


def test_manifest_names_no_command_that_does_not_exist():
    """A classification for a verb the parser does not expose is stale."""
    live = set(LIVE)
    phantom_supported = [c for c in SUPPORTED if c not in live]
    phantom_excluded = [c for c in beta_surface.excluded_commands() if c not in live]
    assert not phantom_supported, f"supported but not in the parser: {phantom_supported}"
    assert not phantom_excluded, f"excluded but not in the parser: {phantom_excluded}"


def test_supported_and_excluded_are_disjoint():
    overlap = set(SUPPORTED) & set(beta_surface.excluded_commands())
    assert not overlap, f"classified both ways: {sorted(overlap)}"


def test_every_exclusion_states_a_reason():
    """'Excluded' without a reason is indistinguishable from an oversight."""
    for command, reason in beta_surface.excluded_commands().items():
        assert reason and len(reason.strip()) > 20, (
            f"{command!r} is excluded without a substantive reason: {reason!r}"
        )


# ---------------------------------------------------------------------------
# Every advertised command actually works
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", SUPPORTED)
def test_supported_command_help_exits_zero(command, tmp_path):
    """`<verb> --help` must exit 0 and print TokenPak's own help.

    Run in an isolated HOME, in a subprocess, because the defect this guards
    against was a wrapper forwarding --help to a child process.
    """
    home = tmp_path / f"home-{command}"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("TOKENPAK_HOME", None)

    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", command, "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=90,
    )

    assert result.returncode == 0, (
        f"`tokenpak {command} --help` exited {result.returncode}\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), f"`tokenpak {command} --help` printed nothing"
    assert "tokenpak" in combined.lower(), (
        f"`tokenpak {command} --help` does not look like TokenPak's own help: {combined[:200]!r}"
    )


@pytest.mark.parametrize("command", ["claude", "codex"])
def test_companion_help_creates_no_state(command, tmp_path):
    """A help request must not provision anything for the wrapped client."""
    home = tmp_path / f"home-{command}"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("TOKENPAK_HOME", None)

    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli", command, "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=90,
    )

    assert result.returncode == 0
    created = sorted(p.name for p in home.iterdir())
    assert created == [], (
        f"`tokenpak {command} --help` created {created} — a help request must "
        "not provision state for a client that may not even be installed"
    )


# ---------------------------------------------------------------------------
# Default discovery advertises only the supported surface
# ---------------------------------------------------------------------------


def test_default_help_advertises_only_supported_commands():
    from tokenpak.cli.commands import help as help_cmd

    advertised = {c["command"] for c in help_cmd.discoverable_commands()}
    off_list = advertised - beta_surface.supported_commands()
    assert not off_list, f"default discovery advertises unsupported commands: {sorted(off_list)}"


def test_argparse_help_advertises_only_supported_commands(capsys):
    """`tokenpak --help` is default discovery too.

    The tiered `help` command derives its listing from the registry; this one
    is a hand-maintained string, which is the drift the allowlist exists to
    catch. It advertised `last` — a stub — as "Show details of last compressed
    request" for a full release after the classification said otherwise.
    """
    from tokenpak._cli_core import _print_quick_help

    _print_quick_help()
    advertised = set(re.findall(r"^ {2}([a-z][a-z-]+) {2,}\S", capsys.readouterr().out, re.M))
    assert advertised, "parsed no commands out of the --help output; the format changed"
    off_list = advertised - beta_surface.supported_commands()
    assert not off_list, f"`tokenpak --help` advertises unsupported commands: {sorted(off_list)}"


def test_essential_and_intermediate_help_are_subsets_of_the_allowlist():
    from tokenpak.cli.commands.help import _ESSENTIAL_COMMANDS, _INTERMEDIATE_COMMANDS

    for name in list(_ESSENTIAL_COMMANDS) + list(_INTERMEDIATE_COMMANDS):
        assert beta_surface.is_supported(name), (
            f"{name!r} is advertised in tiered help but is not on the beta allowlist"
        )


def test_excluded_commands_are_still_reachable():
    """Excluded means undiscovered, not removed — existing scripts keep working."""
    live = live_commands()
    for command in beta_surface.excluded_commands():
        assert command in live, (
            f"{command!r} is classified as excluded but the parser no longer accepts it. "
            "Excluding a command must not remove it; delete the classification instead."
        )


# ---------------------------------------------------------------------------
# Manifest integrity
# ---------------------------------------------------------------------------


def test_manifest_is_valid_json_with_a_policy():
    path = Path(beta_surface._MANIFEST_PATH)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"]
    assert len(data["policy"]) > 100, "the policy must explain what the allowlist means"


def test_manifest_ships_in_the_wheel():
    """A data file that is not packaged would make the CLI fail at runtime."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "beta_surface.json" in pyproject or '"*.json"' in pyproject or "*.json" in pyproject, (
        "beta_surface.json must be included as package data"
    )
