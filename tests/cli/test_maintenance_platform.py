# SPDX-License-Identifier: Apache-2.0
"""Platform-abstraction tests for `tokenpak maintenance` (CP-03/CP-05).

Verifies that proxy restart / log commands:
  - keep full Linux behavior (systemctl / journalctl),
  - degrade honestly on macOS and native Windows (no missing-binary traceback),
  - never shell out to systemctl/journalctl on a platform that lacks them.
"""

from __future__ import annotations

from unittest import mock

import pytest

from tokenpak.cli.commands import maintenance
from tokenpak.platform import process, service


def _force_platform(monkeypatch, label: str) -> None:
    monkeypatch.setattr(process, "current_platform", lambda: label)


# --------------------------------------------------------------------------- #
# service.restart_proxy_service
# --------------------------------------------------------------------------- #


def test_restart_linux_success(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/systemctl")
    run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(service.subprocess, "run", run)

    result = service.restart_proxy_service("tokenpak-proxy.service")

    assert result.supported and result.ok
    args = run.call_args[0][0]
    assert args == ["systemctl", "--user", "restart", "tokenpak-proxy.service"]


def test_restart_linux_failure_is_supported_but_not_ok(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/systemctl")
    monkeypatch.setattr(
        service.subprocess,
        "run",
        mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="boom")),
    )

    result = service.restart_proxy_service()
    assert result.supported and not result.ok


def test_restart_linux_without_systemd_is_degraded(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(service.shutil, "which", lambda _: None)
    run = mock.Mock()
    monkeypatch.setattr(service.subprocess, "run", run)

    result = service.restart_proxy_service()
    assert not result.supported and not result.ok
    run.assert_not_called()  # never shells out when systemctl is absent


@pytest.mark.parametrize("plat", ["macos", "windows", "other"])
def test_restart_non_linux_is_honest_unsupported(monkeypatch, plat):
    _force_platform(monkeypatch, plat)
    run = mock.Mock()
    monkeypatch.setattr(service.subprocess, "run", run)

    result = service.restart_proxy_service()

    assert not result.supported and not result.ok
    assert "tokenpak restart" in result.message
    run.assert_not_called()  # no systemctl on non-Linux


# --------------------------------------------------------------------------- #
# service.proxy_logs
# --------------------------------------------------------------------------- #


def test_logs_linux_uses_journalctl(monkeypatch):
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/journalctl")
    run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="log-line", stderr=""))
    monkeypatch.setattr(service.subprocess, "run", run)

    result = service.proxy_logs("tokenpak-proxy.service", n=10)
    assert result.message == "log-line"
    assert run.call_args[0][0][0] == "journalctl"


@pytest.mark.parametrize("plat", ["macos", "windows"])
def test_logs_non_linux_points_at_logfile(monkeypatch, plat):
    _force_platform(monkeypatch, plat)
    run = mock.Mock()
    monkeypatch.setattr(service.subprocess, "run", run)

    result = service.proxy_logs(n=10)
    assert not result.supported
    assert "watchdog.log" in result.message
    run.assert_not_called()


# --------------------------------------------------------------------------- #
# service.restart_remediation
# --------------------------------------------------------------------------- #


def test_remediation_is_platform_specific(monkeypatch):
    _force_platform(monkeypatch, "linux")
    assert "systemctl" in service.restart_remediation("tokenpak-proxy.service")
    _force_platform(monkeypatch, "macos")
    assert "tokenpak restart" in service.restart_remediation()
    _force_platform(monkeypatch, "windows")
    assert "tokenpak restart" in service.restart_remediation()


# --------------------------------------------------------------------------- #
# maintenance.restart_proxy / show_logs branching
# --------------------------------------------------------------------------- #


def test_maintenance_restart_success_no_exit(monkeypatch):
    monkeypatch.setattr(
        maintenance.service,
        "restart_proxy_service",
        lambda *_a, **_k: service._ServiceResult(True, True, "✓ Proxy service restarted"),
    )
    monkeypatch.setattr(maintenance.time, "sleep", lambda *_a, **_k: None)
    maintenance.restart_proxy()  # must not raise SystemExit


def test_maintenance_restart_supported_failure_exits_1(monkeypatch):
    monkeypatch.setattr(
        maintenance.service,
        "restart_proxy_service",
        lambda *_a, **_k: service._ServiceResult(True, False, "✖ Restart failed"),
    )
    with pytest.raises(SystemExit) as exc:
        maintenance.restart_proxy()
    assert exc.value.code == 1


def test_maintenance_restart_unsupported_does_not_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        maintenance.service,
        "restart_proxy_service",
        lambda *_a, **_k: service._ServiceResult(False, False, "Use: tokenpak restart"),
    )
    maintenance.restart_proxy()  # honest guidance, no traceback / no exit
    assert "tokenpak restart" in capsys.readouterr().out


def test_maintenance_windows_end_to_end_no_systemctl(monkeypatch, capsys):
    """On Windows, restart_proxy prints guidance and never invokes systemctl."""
    _force_platform(monkeypatch, "windows")
    run = mock.Mock()
    monkeypatch.setattr(service.subprocess, "run", run)

    maintenance.restart_proxy()

    run.assert_not_called()
    assert "tokenpak restart" in capsys.readouterr().out
