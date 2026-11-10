# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for companion tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_direct_claude_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep launcher tests independent of a running proxy.

    ``tokenpak claude`` refuses to start Claude when no proxy answers, unless
    ``TOKENPAK_ALLOW_DIRECT`` is set. Most launcher tests exercise file
    generation and argument handling, not proxy readiness, so they run with
    the switch set. Tests of the readiness gate delete it explicitly.
    """
    monkeypatch.setenv("TOKENPAK_ALLOW_DIRECT", "1")
