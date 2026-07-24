# SPDX-License-Identifier: Apache-2.0
"""Regression guard — ``tokenpak doctor`` exit codes.

Restores coverage for ``tokenpak/cli/cli_doctor.py::cmd_doctor``;
``test_doctor_claude_code.py`` exercises a different module.

``cmd_doctor`` keeps a ``{"pass", "warn", "fail"}`` tally and ends with::

    if results["fail"] > 0:
        sys.exit(1)

So a non-zero ``fail`` count is the only thing that turns an unhealthy
install into a non-zero exit. If that branch is dropped, broken installs
report success — the silent-failure surface this test guards.

What this file pins
-------------------
- An invalid-JSON config (a ``fail`` check) → ``SystemExit(1)``.
- An OPEN circuit breaker reported by ``/health`` (a ``fail`` check) →
  ``SystemExit(1)``.
- An unmet Python-version gate (a ``fail`` check) → ``SystemExit(1)``.
- An all-healthy run raises no ``SystemExit`` (exit 0).

Red-when-broken: flip / remove the ``results["fail"] > 0`` exit branch →
the three exit-1 assertions no longer raise and fail.

Hermetic: ``Path.home`` is redirected to ``tmp_path`` and the proxy
``/health`` fetch (``urllib.request.urlopen``) is stubbed, so no real
filesystem home or network is touched.
"""

from __future__ import annotations

import json
import types
from collections import namedtuple

import pytest

from tokenpak.cli import cli_doctor

# A namedtuple stand-in for ``sys.version_info``: compares against plain
# tuples element-wise (like the real object) and exposes .major/.minor/.micro
# for the f-string display in cmd_doctor.
_FakeVersionInfo = namedtuple("_FakeVersionInfo", "major minor micro releaselevel serial")

# A health payload with no failing signals — used by the cases where the
# proxy should look healthy so the only fail comes from the case under test.
_HEALTHY_HEALTH = {
    "status": "ok",
    "requests_total": 0,
    "requests_errors": 0,
    "circuit_breakers": {"enabled": True, "any_open": False, "providers": {}},
}


class _FakeResp:
    """Minimal ``urlopen`` return value: only ``.read()`` is consumed."""

    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload


@pytest.fixture
def doctor_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` to an isolated tmp dir and create ~/.tokenpak."""
    monkeypatch.setattr(cli_doctor.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".tokenpak").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _stub_health(monkeypatch, payload):
    """Stub the proxy /health fetch to return ``payload`` (or raise if None)."""

    def _fake_urlopen(*_args, **_kwargs):
        if payload is None:
            raise OSError("proxy unavailable")
        return _FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)


def _write_config(home, raw: str) -> None:
    (home / ".tokenpak" / "config.json").write_text(raw, encoding="utf-8")


def _args() -> types.SimpleNamespace:
    return types.SimpleNamespace(fix=False)


def test_invalid_config_json_exits_1(doctor_home, monkeypatch):
    _write_config(doctor_home, "{ this is not valid json")
    _stub_health(monkeypatch, _HEALTHY_HEALTH)
    with pytest.raises(SystemExit) as exc:
        cli_doctor.cmd_doctor(_args())
    assert exc.value.code == 1


def test_open_circuit_breaker_exits_1(doctor_home, monkeypatch):
    _write_config(doctor_home, json.dumps({"version": "1.0", "port": 8766}))
    unhealthy = dict(_HEALTHY_HEALTH)
    unhealthy["circuit_breakers"] = {
        "enabled": True,
        "any_open": True,
        "providers": {"anthropic": {"state": "open"}},
    }
    _stub_health(monkeypatch, unhealthy)
    with pytest.raises(SystemExit) as exc:
        cli_doctor.cmd_doctor(_args())
    assert exc.value.code == 1


def test_unmet_python_version_exits_1(doctor_home, monkeypatch):
    _write_config(doctor_home, json.dumps({"version": "1.0", "port": 8766}))
    _stub_health(monkeypatch, _HEALTHY_HEALTH)
    monkeypatch.setattr(cli_doctor.sys, "version_info", _FakeVersionInfo(3, 9, 0, "final", 0))
    with pytest.raises(SystemExit) as exc:
        cli_doctor.cmd_doctor(_args())
    assert exc.value.code == 1


def test_all_healthy_exits_0(doctor_home, monkeypatch):
    _write_config(doctor_home, json.dumps({"version": "1.0", "port": 8766}))
    _stub_health(monkeypatch, _HEALTHY_HEALTH)
    # No failing check → cmd_doctor falls through without sys.exit (exit 0).
    result = cli_doctor.cmd_doctor(_args())
    assert result is None
