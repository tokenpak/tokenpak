# SPDX-License-Identifier: Apache-2.0
"""``tokenpak claude`` waits a bounded time for the proxy, then fails closed.

A missed health probe used to start Claude directly, unproxied, with no error:
no spend guard and no telemetry for the whole session. The launcher now
retries until a deadline and then exits with ``EXIT_RUNTIME_UNAVAILABLE`` and
one line, unless ``TOKENPAK_ALLOW_DIRECT`` explicitly allows a direct start.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from tokenpak.cli.exit_codes import EXIT_RUNTIME_UNAVAILABLE
from tokenpak.companion import launcher
from tokenpak.companion.config import CompanionConfig

PROXY = "http://127.0.0.1:59999"


class _Healthy:
    status_code = 200


@pytest.fixture
def launch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated launch: own journal/run dirs, a fixed proxy URL, short waits."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("TOKENPAK_PROXY_URL", PROXY)
    monkeypatch.setenv("TOKENPAK_PROXY_WAIT_SECONDS", "0.3")
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", "0.05")
    monkeypatch.delenv("TOKENPAK_ALLOW_DIRECT", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(CompanionConfig, "run_dir", property(lambda self: run_dir))
    return run_dir


def _run(args: list[str] | None = None) -> tuple[object, dict[str, str] | None]:
    captured: dict[str, str] = {}
    with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
        mock_exec.side_effect = lambda cmd, argv, env: captured.update(env)
        rc = launcher.main(args or [])
    return rc, (captured if mock_exec.called else None)


def test_missed_probe_retries_then_refuses_with_one_line(launch_env, capsys):
    calls = []

    def refuse(url, timeout):
        calls.append(url)
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=refuse):
        rc, child_env = _run()

    assert rc == EXIT_RUNTIME_UNAVAILABLE
    assert child_env is None, "Claude must not start without the proxy"
    assert len(calls) > 1, "a missed probe is retried before giving up"
    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(err_lines) == 1
    assert PROXY in err_lines[0]
    assert "TOKENPAK_ALLOW_DIRECT=1" in err_lines[0]
    # Refused before anything was generated.
    assert list(launch_env.glob("launch-*")) == []


def test_proxy_that_comes_up_inside_the_wait_routes_claude(launch_env, monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_WAIT_SECONDS", "5")
    answers = iter([httpx.ConnectError("refused"), httpx.ConnectError("refused")])

    def late(url, timeout):
        try:
            raise next(answers)
        except StopIteration:
            return _Healthy()

    with patch.object(httpx, "get", side_effect=late):
        _, child_env = _run()

    assert child_env is not None
    assert child_env["ANTHROPIC_BASE_URL"] == PROXY


def test_direct_start_switch_starts_claude_unproxied_and_says_so(launch_env, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_ALLOW_DIRECT", "1")
    calls = []

    def refuse(url, timeout):
        calls.append(url)
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=refuse):
        _, child_env = _run()

    assert child_env is not None
    assert "ANTHROPIC_BASE_URL" not in child_env
    assert len(calls) == 1, "an allowed direct start does not hold the launch"
    assert "starting Claude directly" in capsys.readouterr().err


def test_inherited_route_is_not_held_or_refused(launch_env, monkeypatch, capsys):
    inherited = "http://127.0.0.1:9999"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", inherited)
    calls = []

    def refuse(url, timeout):
        calls.append(url)
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx, "get", side_effect=refuse):
        _, child_env = _run()

    assert child_env is not None
    assert child_env["ANTHROPIC_BASE_URL"] == inherited
    assert len(calls) == 1
    assert "not selected by TokenPak" in capsys.readouterr().err


def test_wait_is_bounded_by_the_deadline():
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    with patch.object(launcher, "_proxy_healthy", return_value=False) as probe:
        ok = launcher._wait_for_proxy(
            PROXY,
            wait_s=2.0,
            probe_timeout_s=1.0,
            interval_s=0.5,
            clock=lambda: now[0],
            sleep=sleep,
        )

    assert ok is False
    assert sum(slept) == pytest.approx(2.0)
    assert probe.call_count == 5  # t=0, 0.5, 1.0, 1.5, 2.0


def test_zero_wait_probes_once():
    with patch.object(launcher, "_proxy_healthy", return_value=False) as probe:
        assert (
            launcher._wait_for_proxy(
                PROXY, wait_s=0, probe_timeout_s=1.0, interval_s=0.5, sleep=lambda s: None
            )
            is False
        )
    assert probe.call_count == 1


@pytest.mark.parametrize("raw", ["", "abc", "-1", "nan", "inf"])
def test_invalid_durations_fall_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("TOKENPAK_PROXY_WAIT_SECONDS", raw)
    assert launcher._env_seconds("TOKENPAK_PROXY_WAIT_SECONDS", 10.0) == 10.0


def test_zero_probe_timeout_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", "0")
    assert (
        launcher._env_seconds("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", 1.0, allow_zero=False) == 1.0
    )


def test_default_proxy_url_honours_the_configured_port(monkeypatch):
    monkeypatch.delenv("TOKENPAK_PROXY_URL", raising=False)
    monkeypatch.setenv("TOKENPAK_PORT", "9123")
    assert launcher._default_proxy_url() == "http://localhost:9123"


def test_cli_exits_with_the_launcher_refusal_status(monkeypatch):
    from tokenpak import _cli_core

    monkeypatch.setattr(_cli_core, "_require_companion_client", lambda name: None)
    monkeypatch.delenv("TOKENPAK_COMPANION_BUDGET", raising=False)
    with patch("tokenpak.companion.launch", return_value=EXIT_RUNTIME_UNAVAILABLE):
        with pytest.raises(SystemExit) as stopped:
            _cli_core.cmd_claude(argparse.Namespace(args=[], budget=None))
    assert stopped.value.code == EXIT_RUNTIME_UNAVAILABLE
    assert os.environ.get("TOKENPAK_COMPANION_BUDGET") is None


def test_refusal_code_matches_the_documented_exit_code():
    assert launcher._EXIT_RUNTIME_UNAVAILABLE == EXIT_RUNTIME_UNAVAILABLE
