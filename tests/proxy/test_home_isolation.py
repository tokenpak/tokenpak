"""Scoped-TOKENPAK_HOME isolation contract for proxy runtime state.

Regression guard for the benchmark-isolation leak (opus-value-benchmark finding
2026-07-02): a proxy launched with a scoped ``TOKENPAK_HOME`` used to write
``proxy.pid`` into the *fleet* home because the pid/monitor/config/watchdog paths
hardcoded ``Path.home() / ".tokenpak"`` instead of the ``tokenpak._paths``
resolver. These tests pin that fleet runtime-state paths honor ``TOKENPAK_HOME``
and that the default (unscoped) behavior is unchanged.
"""

import importlib
import os
import re
from pathlib import Path

import pytest

from tokenpak import _paths


@pytest.fixture()
def scoped_home(tmp_path, monkeypatch):
    """Point TOKENPAK_HOME at a scratch dir; clear the license override."""
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.delenv("TOKENPAK_LICENSE_FILE", raising=False)
    return tmp_path


# ── Test 1: TOKENPAK_HOME=<tmp> writes proxy.pid under <tmp> ────────────────────
def test_scoped_home_writes_pid_under_home(scoped_home):
    from tokenpak.proxy.server import _write_proxy_pid_file

    pid_path = _write_proxy_pid_file()

    assert pid_path == scoped_home / "proxy.pid"
    assert pid_path.exists()
    assert pid_path.read_text().strip() == str(os.getpid())


# ── Test 2: existing ~/.tokenpak/proxy.pid byte+mtime unchanged ────────────────
def test_fleet_pid_untouched_under_scoped_home(scoped_home):
    from tokenpak.proxy.server import _write_proxy_pid_file

    fleet_pid = Path.home() / ".tokenpak" / "proxy.pid"
    before = (
        (fleet_pid.read_bytes(), fleet_pid.stat().st_mtime_ns)
        if fleet_pid.exists()
        else None
    )

    _write_proxy_pid_file()  # runs under scoped_home

    if before is None:
        # If it didn't exist before, the scoped write must not have created it.
        assert not fleet_pid.exists()
    else:
        assert fleet_pid.read_bytes() == before[0]
        assert fleet_pid.stat().st_mtime_ns == before[1]


# ── Test 3: config.yaml / license.json / monitor.db resolve under scoped home
#            and the fleet copies are byte+mtime unchanged ──────────────────────
def test_scoped_home_state_paths_and_fleet_untouched(scoped_home):
    assert _paths.under("monitor.db") == scoped_home / "monitor.db"
    assert _paths.under("config.yaml") == scoped_home / "config.yaml"
    assert _paths.under("proxy.pid") == scoped_home / "proxy.pid"
    assert _paths.home() == scoped_home

    # Reloading the watchdog under the scoped home resolves its module-level
    # runtime-state constants under the scope (they were the pid/log leak sites).
    monkey_home = scoped_home
    wd = importlib.reload(importlib.import_module("tokenpak.proxy.proxy_watchdog"))
    try:
        assert wd.PROXY_PID_FILE == monkey_home / "proxy.pid"
        assert wd.WATCHDOG_LOG == monkey_home / "watchdog.log"
        assert wd.COOLDOWNS_FILE == monkey_home / "cooldowns.json"
        assert wd.AUTH_PROFILES_FILE == monkey_home / "auth-profiles.json"
    finally:
        # Restore module-level constants to the ambient environment.
        os.environ.pop("TOKENPAK_HOME", None)
        importlib.reload(wd)

    # Resolving scoped paths must never touch the fleet home's files.
    fleet = Path.home() / ".tokenpak"
    for name in ("config.yaml", "license.json", "monitor.db"):
        f = fleet / name
        if f.exists():
            snap = (f.read_bytes(), f.stat().st_mtime_ns)
            _ = _paths.under(name)  # resolution is read-only
            assert f.read_bytes() == snap[0]
            assert f.stat().st_mtime_ns == snap[1]


# ── Test 4: license path scoped under TOKENPAK_HOME / TOKENPAK_LICENSE_FILE ─────
def test_license_path_scoped(scoped_home, monkeypatch):
    from tokenpak.licensing import _license_path

    assert _license_path() == scoped_home / "license.json"

    override = scoped_home / "custom-lic.json"
    monkeypatch.setenv("TOKENPAK_LICENSE_FILE", str(override))
    assert _license_path() == override


# ── Test 5: static guard — no hardcoded Path.home() fleet-runtime path remains ─
def test_no_home_hardcode_for_runtime_state():
    root = Path(__file__).resolve().parents[2] / "tokenpak" / "proxy"
    hardcode = re.compile(
        r'Path\.home\(\)\s*/\s*"\.tokenpak"|expanduser\(\s*"~/\.tokenpak'
    )
    offenders = {}
    for rel in ("server.py", "proxy_watchdog.py", "startup.py"):
        text = (root / rel).read_text()
        hits = [
            ln
            for ln in text.splitlines()
            if hardcode.search(ln) and not ln.lstrip().startswith("#")
        ]
        if hits:
            offenders[rel] = hits
    assert not offenders, f"fleet-home hardcodes remain: {offenders}"


# ── Test 6 (repro-pin + default preservation): unset TOKENPAK_HOME resolves to
#    the legacy ~/.tokenpak home (current fleet behavior). ──────────────────────
def test_default_behavior_preserved_when_home_unset(monkeypatch):
    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    if _paths.canonical_home().exists():
        pytest.skip("~/.tpk present — canonical home active; legacy-default check N/A")
    assert _paths.home() == Path.home() / ".tokenpak"
    assert _paths.under("proxy.pid") == Path.home() / ".tokenpak" / "proxy.pid"
