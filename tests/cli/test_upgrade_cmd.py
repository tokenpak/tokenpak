# SPDX-License-Identifier: Apache-2.0
"""B2 (PM/GTM v2 Phase 3): `tokenpak upgrade` CLI.

Opens the canonical Pro upgrade page in the user's default browser.
KEVIN-DECISION-A (2026-04-23): canonical target is
``https://app.tokenpak.ai/upgrade``; ``tokenpak.ai/paid`` remains the
public marketing surface.

Tests exercise the non-interactive path (``--print-url``) to avoid
launching a real browser and to prove the URL the command would open
matches Kevin's ruling.

Traces to v2 M-B2 per
~/vault/02_COMMAND_CENTER/initiatives/2026-04-23-tokenpak-pm-gtm-readiness-v2/.
"""

from __future__ import annotations

import subprocess
import sys


def test_upgrade_prints_canonical_url_when_env_unset(monkeypatch):
    """Default URL (no env override) must be app.tokenpak.ai/upgrade per KEVIN-A."""
    monkeypatch.delenv("TOKENPAK_UPGRADE_URL", raising=False)

    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "upgrade", "--print-url"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"`tokenpak upgrade --print-url` must exit zero; got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    # Split to ignore any deprecation warnings on stderr.
    assert result.stdout.strip() == "https://app.tokenpak.ai/upgrade", (
        f"default upgrade URL drifted from KEVIN-DECISION-A canonical: "
        f"expected 'https://app.tokenpak.ai/upgrade', got {result.stdout.strip()!r}"
    )


def test_upgrade_honors_env_override():
    """TOKENPAK_UPGRADE_URL overrides the default (test/alternate-deployment path)."""
    test_url = "https://example.test/fake-upgrade"
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak", "upgrade", "--print-url"],
        capture_output=True,
        text=True,
        timeout=10,
        env={**__import__("os").environ, "TOKENPAK_UPGRADE_URL": test_url},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == test_url, (
        f"TOKENPAK_UPGRADE_URL override ignored: got {result.stdout.strip()!r}"
    )


def test_upgrade_registered_in_getting_started_group():
    """`upgrade` must appear in the Getting Started group so `tokenpak help` shows it."""
    from tokenpak.cli._impl import _COMMAND_GROUPS

    getting_started = dict(_COMMAND_GROUPS["Getting Started"])
    assert "upgrade" in getting_started, (
        "`upgrade` missing from _COMMAND_GROUPS['Getting Started']"
    )


def test_upgrade_subparser_dispatches_to_cmd_upgrade():
    """argparse dispatch for `upgrade` must resolve to cmd_upgrade."""
    from tokenpak.cli._impl import build_parser, cmd_upgrade

    parser = build_parser()
    ns = parser.parse_args(["upgrade", "--print-url"])
    assert ns.func is cmd_upgrade
    assert ns.print_url is True
