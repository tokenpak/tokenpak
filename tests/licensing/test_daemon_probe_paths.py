# SPDX-License-Identifier: Apache-2.0
"""Daemon sock-info discovery follows the shared ``tokenpak._paths`` contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from tokenpak import _paths
from tokenpak.licensing import daemon_probe


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv(_paths.ENV_VAR, raising=False)
    monkeypatch.setattr(daemon_probe, "_SOCK_INFO_PATH", None)
    return tmp_path


def _touch(base: Path) -> Path:
    target = base / "pro" / "daemon.sock-info"
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    return target


def test_module_is_the_worktree_copy():
    expected = Path(__file__).resolve().parents[2] / "tokenpak/licensing/daemon_probe.py"
    assert Path(daemon_probe.__file__).resolve() == expected


def test_fresh_home_resolves_canonical(fake_home):
    assert daemon_probe.sock_info_path() == fake_home / ".tpk" / "pro" / "daemon.sock-info"
    assert daemon_probe.probe_daemon() == ("unavailable", "sock_info_absent")


def test_canonical_file_found_without_override(fake_home):
    target = _touch(fake_home / ".tpk")
    assert daemon_probe.sock_info_path() == target
    assert daemon_probe.probe_daemon() == ("unavailable", "sock_info_malformed")


def test_custom_home_wins(fake_home, monkeypatch):
    custom = fake_home / "custom"
    target = _touch(custom)
    _touch(fake_home / ".tpk")
    monkeypatch.setenv(_paths.ENV_VAR, str(custom))
    assert daemon_probe.sock_info_path() == target


def test_legacy_file_found_when_only_legacy(fake_home):
    target = _touch(fake_home / ".tokenpak")
    assert daemon_probe.sock_info_path() == target


def test_explicit_override_still_supported(fake_home):
    _touch(fake_home / ".tpk")
    missing = fake_home / "nope"
    assert daemon_probe.probe_daemon(sock_info_override=missing) == (
        "unavailable",
        "sock_info_absent",
    )


def test_custom_home_without_daemon_does_not_fall_through(fake_home, monkeypatch):
    custom = fake_home / "custom"
    custom.mkdir()
    (custom / "license.json").write_text("{}")
    _touch(fake_home / ".tokenpak")
    _touch(fake_home / ".tpk")
    monkeypatch.setenv(_paths.ENV_VAR, str(custom))
    assert daemon_probe.sock_info_path() == custom / "pro" / "daemon.sock-info"
    assert daemon_probe.probe_daemon() == ("unavailable", "sock_info_absent")


def test_selected_canonical_without_daemon_does_not_fall_through(fake_home):
    canonical = fake_home / ".tpk"
    canonical.mkdir()
    (canonical / "license.json").write_text("{}")
    _touch(fake_home / ".tokenpak")
    assert daemon_probe.sock_info_path() == canonical / "pro" / "daemon.sock-info"
    assert daemon_probe.probe_daemon() == ("unavailable", "sock_info_absent")
