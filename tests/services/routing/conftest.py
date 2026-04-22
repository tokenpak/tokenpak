"""Classifier tests must run with a clean env.

The test suite often runs inside Claude Code itself, which sets
``CLAUDECODE=1`` + ``CLAUDE_CODE_ENTRYPOINT`` in the environment.
Those env markers are fallback signals for the classifier (priority
5 below the header-based signals), but leaving them set would make
"generic user on a generic machine" test cases spuriously classify as
Claude Code. Autouse fixture clears them for every test in this dir.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_claude_code_env(monkeypatch):
    for key in (
        "CLAUDECODE",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_ARGV",
    ):
        monkeypatch.delenv(key, raising=False)
