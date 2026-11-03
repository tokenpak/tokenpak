# SPDX-License-Identifier: Apache-2.0
"""Capture the Claude child launch environment without starting a client."""

from unittest.mock import patch

import httpx
import pytest

from tokenpak.companion import launcher
from tokenpak.companion.config import CompanionConfig
from tokenpak.proxy.spend_guard.unattended import ENV, HEADER


@pytest.mark.parametrize("opt_in", ["0", "1"])
def test_claude_launch_forwards_unattended_marker_only_when_enabled(tmp_path, monkeypatch, opt_in):
    monkeypatch.setenv(ENV, opt_in)
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("TOKENPAK_PROXY_URL", "http://fixture-proxy:8766")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Custom: kept")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with patch.object(CompanionConfig, "run_dir", property(lambda _: run_dir)):
        with patch.object(httpx, "get", return_value=type("Healthy", (), {"status_code": 200})()):
            with patch.object(launcher.os, "execvpe") as child:
                launcher.main([])
    env = child.call_args[0][2]
    assert env["ANTHROPIC_BASE_URL"] == "http://fixture-proxy:8766"
    assert "X-Custom: kept" in env["ANTHROPIC_CUSTOM_HEADERS"]
    assert (f"{HEADER}: 1" in env["ANTHROPIC_CUSTOM_HEADERS"]) == (opt_in == "1")
