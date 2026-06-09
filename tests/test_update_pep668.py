# SPDX-License-Identifier: Apache-2.0
"""Tests for PEP 668-tolerant ``tokenpak update`` package upgrade.

``_pip_upgrade_tokenpak`` must retry into the user site with
``--break-system-packages`` when a plain ``pip install`` is refused on an
externally-managed interpreter (PEP 668), and must defer to ``pipx upgrade``
when running inside a pipx-managed venv. All cases keep CI offline — no real
pip invocation happens (``subprocess.run`` is always mocked).
"""

import subprocess

from tokenpak import _cli_core

PEP668_STDERR = (
    "error: externally-managed-environment\n\n"
    "× This environment is externally managed\n"
    "╰─> To install Python packages system-wide, try apt install ...\n"
)


class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_pep668_retries_with_break_system_packages(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--break-system-packages" in cmd:
            return _FakeProc(0)
        return _FakeProc(1, stderr=PEP668_STDERR)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, method, detail = _cli_core._pip_upgrade_tokenpak(verbose=False)

    assert ok is True
    assert method == "pip-bsp"
    assert len(calls) == 2
    # First attempt is plain; only the retry carries --break-system-packages.
    assert "--break-system-packages" not in calls[0]
    assert "--break-system-packages" in calls[1]
    # Always targets the running interpreter and the tokenpak package.
    assert calls[1][:3] == [_cli_core.sys.executable, "-m", "pip"]
    assert calls[1][-1] == "tokenpak"


def test_clean_success_no_retry(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, method, detail = _cli_core._pip_upgrade_tokenpak(verbose=False)

    assert ok is True
    assert method == "pip"
    assert len(calls) == 1
    assert "--break-system-packages" not in calls[0]


def test_pipx_install_defers_without_calling_pip(monkeypatch):
    monkeypatch.setattr(_cli_core.sys, "prefix", "/home/u/.local/pipx/venvs/tokenpak")

    def fail_run(cmd, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("pip must not run for a pipx-managed install")

    monkeypatch.setattr(subprocess, "run", fail_run)
    ok, method, detail = _cli_core._pip_upgrade_tokenpak(verbose=False)

    assert ok is False
    assert method == "pipx"


def test_non_pep668_failure_surfaces_detail(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(1, stderr="ERROR: Could not find a version that satisfies ...")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, method, detail = _cli_core._pip_upgrade_tokenpak(verbose=False)

    assert ok is False
    assert method == "pip"
    assert "Could not find a version" in detail


def test_user_install_detection(monkeypatch, tmp_path):
    import site

    import tokenpak as _tp

    monkeypatch.setattr(site, "getuserbase", lambda: str(tmp_path / ".local"))
    monkeypatch.setattr(
        _tp, "__file__", str(tmp_path / ".local" / "lib" / "tokenpak" / "__init__.py")
    )
    assert _cli_core._tokenpak_is_user_install() is True

    monkeypatch.setattr(
        _tp, "__file__", "/usr/lib/python3/dist-packages/tokenpak/__init__.py"
    )
    assert _cli_core._tokenpak_is_user_install() is False
