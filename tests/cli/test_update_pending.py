# SPDX-License-Identifier: Apache-2.0
"""Pending-update detection, ``tokenpak update apply``, status, doctor, footer."""

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from tokenpak.cli.commands import doctor as doc
from tokenpak.cli.commands import update_apply
from tokenpak.cli.exit_codes import EXIT_BUSY, EXIT_FAILURE, EXIT_MISSING_PREREQUISITE, EXIT_OK
from tokenpak.core.runtime import update_pending as up


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(up, "_loaded_version", lambda: "1.30.2")
    monkeypatch.setattr(up, "_disk_version", lambda: "1.30.2")
    monkeypatch.setattr(up, "_health_version", lambda timeout=0.5: None)
    monkeypatch.setattr(up, "_footer_cache", (0.0, ""))
    return tmp_path


def _write_marker(version="1.30.3", **extra):
    path = up.marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": up.MARKER_SCHEMA,
        "release": {"oss": version, "paid": "0.6.0"},
        "stack_units": ["tokenpak-proxy.service", "tokenpak-pro-daemon.service"],
    }
    body.update(extra)
    path.write_text(json.dumps(body))
    return path


# --- detection ---------------------------------------------------------------


def test_not_pending_by_default():
    found = up.detect()
    assert found.pending is False
    assert found.summary() == ""


def test_staged_marker_is_pending():
    _write_marker("1.30.3")
    found = up.detect()
    assert (found.pending, found.source, found.running, found.target) == (
        True,
        "staged",
        "1.30.2",
        "1.30.3",
    )
    assert found.summary() == "1.30.2 → 1.30.3, applies at next launch"


def test_staged_compares_against_running_proxy(monkeypatch):
    _write_marker("1.30.3")
    monkeypatch.setattr(up, "_health_version", lambda timeout=0.5: "1.30.3")
    assert up.detect().pending is False


def test_stale_marker_not_newer_is_ignored():
    _write_marker("1.30.2")
    assert up.detect().pending is False


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", json.dumps({"schema": "other/1", "release": {"oss": "9.9.9"}})],
)
def test_malformed_marker_is_ignored(text):
    path = up.marker_path()
    path.parent.mkdir(parents=True)
    path.write_text(text)
    assert up.detect().pending is False


def test_marker_with_non_version_string_is_ignored():
    _write_marker("1.30.3; rm -rf /")
    assert up.detect().pending is False


def test_marker_symlink_is_ignored(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"schema": up.MARKER_SCHEMA, "release": {"oss": "1.30.3"}}))
    link = up.marker_path()
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert up.detect().pending is False


def test_installed_newer_than_running_proxy(monkeypatch):
    monkeypatch.setattr(up, "_disk_version", lambda: "1.30.3")
    monkeypatch.setattr(up, "_health_version", lambda timeout=0.5: "1.30.2")
    found = up.detect()
    assert (found.pending, found.source, found.target) == (True, "installed", "1.30.3")
    assert found.summary() == "1.30.2 → 1.30.3, restart to load it"


def test_installed_source_needs_a_running_proxy(monkeypatch):
    monkeypatch.setattr(up, "_disk_version", lambda: "1.30.3")
    assert up.detect().pending is False  # proxy down: nothing stale is running


def test_detect_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(up, "_read_marker", boom)
    assert up.detect().pending is False


def test_stack_units_filters_unsafe_names():
    _write_marker(stack_units=["ok.service", "bad name; reboot"])
    assert up.stack_units() == []
    _write_marker(stack_units=["a.service", "b.service"])
    assert up.stack_units() == ["a.service", "b.service"]


# --- footer ------------------------------------------------------------------


def test_footer_marker_empty_when_not_pending():
    assert up.footer_marker() == ""


def test_footer_marker_text_and_render():
    from tokenpak.telemetry.footer import render_footer, render_footer_oneline
    from tokenpak.telemetry.proxy_collector import RequestStats

    stats = RequestStats(
        request_id="r",
        timestamp=__import__("datetime").datetime.now(),
        input_tokens_raw=10,
        input_tokens_sent=10,
        tokens_saved=0,
        percent_saved=0.0,
        cost_saved=0.0,
    )
    clean_one, clean_multi = render_footer_oneline(stats), render_footer(stats)
    assert "update" not in clean_one and "update" not in clean_multi

    _write_marker("1.30.3")
    up._footer_cache = (0.0, "")
    assert up.footer_marker() == "↑ update 1.30.3 pending"
    assert render_footer_oneline(stats) == f"{clean_one} | ↑ update 1.30.3 pending"
    assert "↑ update 1.30.3 pending" in render_footer(stats)


# --- doctor ------------------------------------------------------------------


def test_doctor_update_state_and_row():
    _write_marker("1.30.3")
    assert doc._update_state() == ("pending", "1.30.3")
    panel = doc.build_lifecycle_summary(
        version="1.30.2",
        setup_present=True,
        route_state="active",
        proxy_state="running",
        update_state="pending",
        update_latest="1.30.3",
    )
    assert "1.30.3 pending" in panel and "tokenpak update apply" in panel


# --- status ------------------------------------------------------------------


def test_status_runtime_frame_shows_update_row(capsys, monkeypatch):
    from tokenpak.cli.commands import status

    _write_marker("1.30.3")
    status._print_runtime_and_routing("http://127.0.0.1:1", uptime_s=0, errors=0, requests=0)
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.strip().startswith("Update"))
    assert "1.30.2 → 1.30.3, applies at next launch" in line
    assert "tokenpak update apply" in line


def test_status_runtime_frame_silent_when_not_pending(capsys):
    from tokenpak.cli.commands import status

    status._print_runtime_and_routing("http://127.0.0.1:1", uptime_s=0, errors=0, requests=0)
    assert "Update" not in capsys.readouterr().out


# --- update apply ------------------------------------------------------------


def _args(check=False):
    return argparse.Namespace(apply_check=check)


class _Run:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_apply_nothing_pending(capsys):
    assert update_apply.run(_args()) == EXIT_OK
    assert "No update is pending" in capsys.readouterr().out


def test_apply_refuses_when_requests_in_flight(capsys, monkeypatch):
    _write_marker("1.30.3")
    from tokenpak.core.runtime import lifecycle

    monkeypatch.setattr(
        lifecycle, "probe_health", lambda port, timeout=2.0: (True, {"in_flight_requests": 2})
    )
    called = []
    monkeypatch.setattr(update_apply, "_systemctl", lambda *a: called.append(a) or _Run())
    assert update_apply.run(_args()) == EXIT_BUSY
    out = capsys.readouterr().out
    assert "2 requests in flight" in out and "Nothing was changed" in out
    assert called == []


def test_apply_refuses_when_client_connected(capsys, monkeypatch):
    _write_marker("1.30.3")
    from tokenpak.core.runtime import lifecycle

    monkeypatch.setattr(
        lifecycle, "probe_health", lambda port, timeout=2.0: (True, {"in_flight_requests": 0})
    )
    monkeypatch.setattr(update_apply, "established_connections", lambda port: 1)
    assert update_apply.run(_args()) == EXIT_BUSY
    assert "1 client is connected" in capsys.readouterr().out


def _idle(monkeypatch):
    from tokenpak.core.runtime import lifecycle

    monkeypatch.setattr(lifecycle, "probe_health", lambda port, timeout=2.0: (False, {}))


def test_apply_check_is_a_dry_run(capsys, monkeypatch):
    _write_marker("1.30.3")
    _idle(monkeypatch)
    monkeypatch.setattr(update_apply, "_units_loaded", lambda units: True)
    monkeypatch.setattr(update_apply, "_systemctl", lambda *a: pytest.fail("must not restart"))
    assert update_apply.run(_args(check=True)) == EXIT_OK
    out = capsys.readouterr().out
    assert "Dry run" in out and "tokenpak-pro-daemon.service" in out


def test_apply_no_service_manager_prints_manual_step(capsys, monkeypatch):
    _write_marker("1.30.3")
    _idle(monkeypatch)
    monkeypatch.setattr(update_apply, "_units_loaded", lambda units: False)
    assert update_apply.run(_args()) == EXIT_MISSING_PREREQUISITE
    out = capsys.readouterr().out
    assert "systemctl --user stop tokenpak-proxy.service tokenpak-pro-daemon.service" in out


def test_apply_staged_stops_then_starts_whole_stack(capsys, monkeypatch):
    _write_marker("1.30.3")
    _idle(monkeypatch)
    monkeypatch.setattr(update_apply, "_units_loaded", lambda units: True)
    calls = []
    monkeypatch.setattr(update_apply, "_systemctl", lambda *a: calls.append(a) or _Run())
    monkeypatch.setattr(update_apply, "_wait_for_version", lambda target: "1.30.3")
    assert update_apply.run(_args()) == EXIT_OK
    units = ("tokenpak-proxy.service", "tokenpak-pro-daemon.service")
    assert calls == [("stop", *units), ("start", *units)]
    assert "TokenPak 1.30.3 is running" in capsys.readouterr().out


def test_apply_installed_restarts_proxy_unit(capsys, monkeypatch):
    monkeypatch.setattr(up, "_disk_version", lambda: "1.30.3")
    monkeypatch.setattr(up, "_health_version", lambda timeout=0.5: "1.30.2")
    from tokenpak.core.runtime import lifecycle

    monkeypatch.setattr(lifecycle, "probe_health", lambda port, timeout=2.0: (True, {}))
    monkeypatch.setattr(update_apply, "established_connections", lambda port: 0)
    monkeypatch.setattr(update_apply, "_units_loaded", lambda units: True)
    calls = []
    monkeypatch.setattr(update_apply, "_systemctl", lambda *a: calls.append(a) or _Run())
    monkeypatch.setattr(update_apply, "_wait_for_version", lambda target: "1.30.3")
    assert update_apply.run(_args()) == EXIT_OK
    assert calls == [("restart", "tokenpak-proxy.service")]


def test_apply_restart_failure_is_reported(capsys, monkeypatch):
    _write_marker("1.30.3")
    _idle(monkeypatch)
    monkeypatch.setattr(update_apply, "_units_loaded", lambda units: True)
    monkeypatch.setattr(update_apply, "_systemctl", lambda *a: _Run(1, "", "boom"))
    assert update_apply.run(_args()) == EXIT_FAILURE
    assert "Restart failed" in capsys.readouterr().out


def test_established_connections_counts_only_inbound_established(tmp_path, monkeypatch):
    table = tmp_path / "tcp"
    port = 0x2230
    rows = (
        "  sl local rem st\n"
        f"   0: 0100007F:{port:04X} 0100007F:C350 01\n"
        f"   1: 0100007F:{port:04X} 0100007F:C351 06\n"
        f"   2: 0100007F:C352 0100007F:{port:04X} 01\n"
    )
    table.write_text(rows)
    real_open = open

    def fake_open(name, *a, **k):
        return real_open(table if name == "/proc/net/tcp" else "/nonexistent", *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert update_apply.established_connections(port) == 1


# --- CLI wiring --------------------------------------------------------------


def test_update_apply_is_registered_and_help_lists_it(capsys):
    from tokenpak._cli_core import build_parser

    parser = build_parser()
    ns = parser.parse_args(["update", "apply", "--check"])
    assert ns.update_action == "apply" and ns.apply_check is True
    ns = parser.parse_args(["update", "--check"])
    assert getattr(ns, "update_action", None) is None and ns.check is True


def test_update_command_reports_pending_instead_of_downloading(capsys, monkeypatch):
    from tokenpak import _cli_core

    _write_marker("1.30.3")
    monkeypatch.setattr(
        _cli_core, "_fetch_latest_pypi_version", lambda timeout=5: pytest.fail("no network")
    )
    _cli_core.cmd_update(
        argparse.Namespace(
            check=True,
            enable_checks=False,
            disable_checks=False,
            check_status=False,
            force=False,
            core_only=False,
            dry_run=False,
            update_action=None,
        )
    )
    out = capsys.readouterr().out
    assert "Update pending: 1.30.2 → 1.30.3, applies at next launch" in out
    assert "tokenpak update apply" in out
