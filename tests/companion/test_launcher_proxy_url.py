# SPDX-License-Identifier: Apache-2.0
"""Every launcher routes through the proxy address the install configures.

The native Windows Codex launcher used to rebuild its URL from a fixed
loopback address and ``TOKENPAK_PORT``, so a configured ``TOKENPAK_PROXY_URL``
was ignored and the session probed and routed to the default port. The
POSIX Claude launcher ignored ``TOKENPAK_PORT`` and the POSIX Codex launcher
used a fixed URL. All four launchers now resolve the address through one
shared function at launch time.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from tokenpak.companion import launcher, windows_claude
from tokenpak.companion.codex import launcher as codex_launcher
from tokenpak.companion.codex import windows_launcher
from tokenpak.companion.config import CompanionConfig

_HEALTH = json.dumps({"status": "ok", "version": "test"}).encode()
_DEFAULT = object()  # resolved from the shared defaults inside the test


def _expected(value: object) -> str:
    if value is not _DEFAULT:
        return str(value)
    # Imported here so a source tree without the shared module fails the
    # case instead of the whole file.
    from tokenpak.companion import _proxy_endpoint as endpoint

    return f"http://{endpoint.DEFAULT_PROXY_HOST}:{endpoint.DEFAULT_PROXY_PORT}"


# (environment, expected base URL)
CONFIGURATIONS = [
    pytest.param(
        {"TOKENPAK_PROXY_URL": "http://127.0.0.1:54321"}, "http://127.0.0.1:54321", id="url"
    ),
    pytest.param(
        {"TOKENPAK_PROXY_URL": "http://127.0.0.1:54321/", "TOKENPAK_PORT": "9123"},
        "http://127.0.0.1:54321",
        id="url-wins-over-port",
    ),
    pytest.param({"TOKENPAK_PORT": "9123"}, "http://127.0.0.1:9123", id="port"),
    pytest.param({}, _DEFAULT, id="default"),
]


class _Response(io.BytesIO):
    status = 200


def _answer(urls: list[str], timeouts: list[float]):
    def urlopen(url, timeout):
        urls.append(url)
        timeouts.append(timeout)
        return _Response(_HEALTH)

    return urlopen


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No ambient proxy settings, profile or home leak into a launch."""
    for name in (
        "TOKENPAK_PROXY_URL",
        "TOKENPAK_PORT",
        "TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS",
        "ANTHROPIC_BASE_URL",
        "APPDATA",
    ):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TOKENPAK_HOME", str(home))
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(home / "companion"))
    monkeypatch.setattr(windows_launcher._paths, "home", lambda: home)
    binary = tmp_path / "codex.exe"
    binary.write_text("not executed")
    monkeypatch.setenv("TOKENPAK_CODEX_BINARY", str(binary))
    return home


def _windows_codex(monkeypatch) -> tuple[list[str], list[float], list[list[str]]]:
    urls: list[str] = []
    timeouts: list[float] = []
    calls: list[list[str]] = []
    monkeypatch.setattr(windows_launcher, "urlopen", _answer(urls, timeouts))
    monkeypatch.setattr(
        windows_launcher.subprocess, "call", lambda argv, **kw: calls.append(argv) or 0
    )
    assert windows_launcher.main([]) == 0
    return urls, timeouts, calls


def _windows_claude(monkeypatch) -> tuple[list[str], list[float], str]:
    urls: list[str] = []
    timeouts: list[float] = []
    routed: list[str] = []
    monkeypatch.setattr(windows_claude, "urlopen", _answer(urls, timeouts))
    monkeypatch.setattr(sys, "argv", ["tokenpak-claude"])
    with patch.dict(os.environ):  # prepare_environment pins values in os.environ
        with patch(
            "tokenpak.companion.launcher.main",
            side_effect=lambda args: routed.append(os.environ["TOKENPAK_PROXY_URL"]) or 0,
        ):
            assert windows_claude.main() == 0
    return urls, timeouts, routed[0]


# ---------------------------------------------------------------------------
# Regression: the configured URL reaches native Windows Codex
# ---------------------------------------------------------------------------


def test_windows_modules_import_without_platform_operations():
    code = """import subprocess, urllib.request, os
from unittest.mock import patch
with patch.object(subprocess, "Popen", side_effect=AssertionError("spawn during import")), \\
        patch.object(urllib.request, "urlopen", side_effect=AssertionError("network during import")), \\
        patch.object(os, "execvpe", side_effect=AssertionError("exec during import")):
    import tokenpak.companion.windows_claude
    import tokenpak.companion.codex.windows_launcher
    import tokenpak.companion.hooks.windows_native
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_windows_codex_honors_configured_proxy_url(isolated, monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_URL", "http://127.0.0.1:54321")
    urls, _timeouts, calls = _windows_codex(monkeypatch)
    assert urls == ["http://127.0.0.1:54321/health"]
    assert any("http://127.0.0.1:54321/v1" in arg for arg in calls[0])


def test_windows_codex_starts_the_proxy_on_the_configured_port(isolated, monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_URL", "http://127.0.0.1:54321")
    answers = iter([OSError("refused")])

    def urlopen(url, timeout):
        try:
            raise next(answers)
        except StopIteration:
            return _Response(_HEALTH)

    calls: list[list[str]] = []
    monkeypatch.setattr(windows_launcher, "urlopen", urlopen)
    monkeypatch.setattr(
        windows_launcher.subprocess, "call", lambda argv, **kw: calls.append(argv) or 0
    )
    assert windows_launcher.main([]) == 0
    start = calls[0]
    assert start[start.index("start") + 1 :][:2] == ["--port", "54321"]


# ---------------------------------------------------------------------------
# Invariant: one configured value routes every launcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("configured,expected", CONFIGURATIONS)
def test_windows_codex_routes_to_the_configured_proxy(isolated, monkeypatch, configured, expected):
    expected = _expected(expected)
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    urls, _timeouts, calls = _windows_codex(monkeypatch)
    assert urls == [expected + "/health"]
    assert f"model_providers.tokenpak_local.base_url={json.dumps(expected + '/v1')}" in calls[0]


@pytest.mark.parametrize("configured,expected", CONFIGURATIONS)
def test_windows_claude_routes_to_the_configured_proxy(isolated, monkeypatch, configured, expected):
    expected = _expected(expected)
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    urls, _timeouts, routed = _windows_claude(monkeypatch)
    assert urls == [expected + "/health"]
    assert routed == expected


@pytest.mark.parametrize("configured,expected", CONFIGURATIONS)
def test_posix_claude_routes_to_the_configured_proxy(
    isolated, tmp_path, monkeypatch, configured, expected
):
    expected = _expected(expected)
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TOKENPAK_ALLOW_DIRECT", "1")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(CompanionConfig, "run_dir", property(lambda self: run_dir))
    probed: list[str] = []

    class _Healthy:
        status_code = 200

    def get(url, timeout):
        probed.append(url)
        return _Healthy()

    child: dict[str, str] = {}
    with patch.object(httpx, "get", side_effect=get):
        with patch("tokenpak.companion.launcher.os.execvpe") as exec_:
            exec_.side_effect = lambda cmd, argv, env: child.update(env)
            launcher.main([])
    assert probed and set(probed) == {expected + "/health"}
    assert child["ANTHROPIC_BASE_URL"] == expected


@pytest.mark.parametrize("configured,expected", CONFIGURATIONS)
def test_posix_codex_routes_to_the_configured_proxy(isolated, monkeypatch, configured, expected):
    expected = _expected(expected)
    for name, value in configured.items():
        monkeypatch.setenv(name, value)
    urls: list[str] = []
    timeouts: list[float] = []
    with patch("urllib.request.urlopen", side_effect=_answer(urls, timeouts)):
        args, routed = codex_launcher._with_tokenpak_proxy_route(["exec", "hi"])
    assert routed is True
    assert urls == [expected + "/health"]
    assert f'model_providers.tokenpak.base_url="{expected}/v1"' in args


# ---------------------------------------------------------------------------
# Probe timeouts are settings, and bound the probe in wall-clock time
# ---------------------------------------------------------------------------


def test_probe_timeouts_default_to_each_launchers_value(isolated, monkeypatch):
    _urls, codex_win, _calls = _windows_codex(monkeypatch)
    _urls, claude_win, _routed = _windows_claude(monkeypatch)
    posix: list[float] = []
    with patch("urllib.request.urlopen", side_effect=_answer([], posix)):
        codex_launcher._local_proxy_is_healthy()
    assert codex_win == [windows_launcher.DEFAULT_PROBE_TIMEOUT_S]
    assert claude_win == [windows_claude.DEFAULT_PROBE_TIMEOUT_S]
    assert posix == [codex_launcher._DEFAULT_PROBE_TIMEOUT_S]


def test_probe_timeout_setting_reaches_every_probe(isolated, monkeypatch):
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", "1.25")
    _urls, codex_win, _calls = _windows_codex(monkeypatch)
    _urls, claude_win, _routed = _windows_claude(monkeypatch)
    posix: list[float] = []
    with patch("urllib.request.urlopen", side_effect=_answer([], posix)):
        codex_launcher._local_proxy_is_healthy()
    assert codex_win == claude_win == posix == [1.25]


def test_a_slow_health_reply_cannot_hold_the_windows_codex_launch(isolated, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS", "0.1")

    def stalled(url, timeout):
        time.sleep(2.0)
        return _Response(_HEALTH)

    monkeypatch.setattr(windows_launcher, "urlopen", stalled)
    monkeypatch.setattr(windows_launcher.subprocess, "call", lambda argv, **kw: 0)
    started = time.monotonic()
    assert windows_launcher.main([]) == 1
    assert time.monotonic() - started < 1.5
    assert "did not become healthy" in capsys.readouterr().err
