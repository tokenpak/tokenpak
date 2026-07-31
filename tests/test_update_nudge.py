# SPDX-License-Identifier: Apache-2.0
"""Hermetic coverage for the interactive update-available Update/Skip flow."""

import builtins
import json
import multiprocessing
import os
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tokenpak import _cli_core
from tokenpak.core import _update_check


class _TTY:
    def isatty(self):
        return True


class _NotTTY:
    def isatty(self):
        return False


def _cache_at(monkeypatch, tmp_path):
    path = tmp_path / "update_check.json"
    monkeypatch.setattr(_cli_core, "_update_cache_path", lambda: path)
    return path


def _consented(monkeypatch):
    monkeypatch.setattr(_cli_core, "_automatic_update_checks_enabled", lambda: True)


def _hold_update_cache_lock(cache_path, entered, release):
    """Spawn-safe helper proving the lock excludes a distinct process."""
    _cli_core._update_cache_path = lambda: Path(cache_path)
    with _cli_core._update_cache_lock():
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release update-cache lock")


def _acquire_update_cache_lock(cache_path, entered):
    """Spawn-safe contender for the cross-process lock test."""
    _cli_core._update_cache_path = lambda: Path(cache_path)
    with _cli_core._update_cache_lock():
        entered.set()


def test_update_cache_write_is_atomic_private_and_backward_compatible(monkeypatch, tmp_path):
    home = tmp_path / "new-tokenpak-home"
    path = _cache_at(monkeypatch, home)

    _cli_core._write_update_cache("2.0.0", checked_at=123.0)

    assert _cli_core._read_update_cache() == (123.0, "2.0.0")
    assert json.loads(path.read_text()) == {"checked_at": 123.0, "latest": "2.0.0"}
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_suffix(".lock").stat().st_mode) == 0o600
    assert list(home.glob("*.tmp")) == []


def test_failed_probe_is_negative_cached_for_offline_users(monkeypatch, tmp_path):
    _cache_at(monkeypatch, tmp_path)
    assert _cli_core._set_automatic_update_checks(True)
    calls = []

    def fail(timeout):
        calls.append(timeout)
        raise OSError("offline")

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", fail)
    assert _cli_core._latest_for_update_nudge(now=100.0)[0] is None
    assert _cli_core._latest_for_update_nudge(now=100.0 + 23 * 60 * 60)[0] is None
    assert calls == [_cli_core._UPDATE_NUDGE_TIMEOUT_SECONDS]
    assert _cli_core._latest_for_update_nudge(now=100.0 + 24 * 60 * 60)[0] is None
    assert calls == [
        _cli_core._UPDATE_NUDGE_TIMEOUT_SECONDS,
        _cli_core._UPDATE_NUDGE_TIMEOUT_SECONDS,
    ]


def test_successful_probe_is_cached_for_24_hours(monkeypatch, tmp_path):
    _cache_at(monkeypatch, tmp_path)
    assert _cli_core._set_automatic_update_checks(True)
    calls = []

    def latest(timeout):
        calls.append(timeout)
        return "999.0.0"

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", latest)
    assert _cli_core._latest_for_update_nudge(now=100.0)[0] == "999.0.0"
    assert _cli_core._latest_for_update_nudge(now=100.0 + 86399)[0] == "999.0.0"
    assert len(calls) == 1
    assert _cli_core._latest_for_update_nudge(now=100.0 + 86400)[0] == "999.0.0"
    assert len(calls) == 2


def test_automatic_attempt_is_recorded_before_network(monkeypatch, tmp_path):
    path = _cache_at(monkeypatch, tmp_path)
    assert _cli_core._set_automatic_update_checks(True)

    def inspect_then_fail(timeout):
        state = json.loads(path.read_text())
        assert state["checked_at"] == 100.0
        assert state["latest"] is None
        raise OSError("offline")

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", inspect_then_fail)
    assert _cli_core._latest_for_update_nudge(now=100.0)[0] is None


def test_default_no_consent_makes_no_request(monkeypatch, tmp_path, capsys):
    path = _cache_at(monkeypatch, tmp_path)
    prompts = []
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: prompts.append(prompt) or "")

    def fail():
        raise AssertionError("automatic PyPI request occurred before consent")

    monkeypatch.setattr(_cli_core, "_latest_for_update_nudge", fail)
    _cli_core._maybe_prompt_for_update("status")

    assert json.loads(path.read_text())["automatic_checks"] is False
    output = capsys.readouterr().out
    assert prompts == ["    Enable daily update checks? [y/N]: "]
    assert "no TokenPak project, prompt, completion, usage, credential" in output
    assert "file, tool-inventory, vault, or proxy-log data" in output
    assert "ordinary HTTPS/TLS transport metadata" in output
    assert "TLS handshake metadata, and HTTP headers" in output


def test_affirmative_consent_is_saved_before_first_probe(monkeypatch, tmp_path):
    path = _cache_at(monkeypatch, tmp_path)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "yes")

    def inspect_consent():
        state = json.loads(path.read_text())
        assert state["automatic_checks"] is True
        return None, state

    monkeypatch.setattr(_cli_core, "_latest_for_update_nudge", inspect_consent)
    _cli_core._maybe_prompt_for_update("status")


def test_failed_consent_persistence_makes_no_request(monkeypatch, tmp_path, capsys):
    _cache_at(monkeypatch, tmp_path)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "yes")
    monkeypatch.setattr(
        _cli_core,
        "_write_update_cache_unlocked",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only home")),
    )

    def fail():
        raise AssertionError("request occurred without durable consent")

    monkeypatch.setattr(_cli_core, "_latest_for_update_nudge", fail)
    _cli_core._maybe_prompt_for_update("status")

    assert "no check was made" in capsys.readouterr().out


def test_saved_decline_never_reprompts_or_probes(monkeypatch, tmp_path):
    _cache_at(monkeypatch, tmp_path)
    assert _cli_core._set_automatic_update_checks(False)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)

    def fail(*args, **kwargs):
        raise AssertionError("saved decline was ignored")

    monkeypatch.setattr(builtins, "input", fail)
    monkeypatch.setattr(_cli_core, "_latest_for_update_nudge", fail)
    _cli_core._maybe_prompt_for_update("status")


def test_noninteractive_invocation_never_probes_or_prompts(monkeypatch):
    monkeypatch.setattr(_cli_core.sys, "stdin", _NotTTY())
    monkeypatch.setattr(_cli_core.sys, "stdout", _TTY())

    def fail():
        raise AssertionError("noninteractive invocation attempted an update probe")

    monkeypatch.setattr(_cli_core, "_latest_for_update_nudge", fail)
    _cli_core._maybe_prompt_for_update("status")


def test_machine_output_and_update_command_never_prompt(monkeypatch):
    monkeypatch.setattr(_cli_core.sys, "stdin", _TTY())
    monkeypatch.setattr(_cli_core.sys, "stdout", _TTY())
    assert _cli_core._update_nudge_allowed("status", machine_output=True) is False
    assert _cli_core._update_nudge_allowed("update", machine_output=False) is False


def test_parser_json_destinations_and_server_commands_are_excluded(monkeypatch):
    parser = _cli_core.build_parser()
    for argv in (
        ("debug", "list", "--json"),
        ("fingerprint", "cache", "--json"),
        ("telemetry", "export"),
        ("telemetry", "export", "--format", "csv"),
        ("cost", "--export-csv"),
        ("goals", "export"),
        ("goals", "export", "--output", "goals.json"),
        ("report", "--markdown"),
        ("upgrade", "--print-url"),
        ("help", "--minimal"),
    ):
        assert _cli_core._machine_output_requested(parser.parse_args(argv)) is True

    monkeypatch.setattr(_cli_core.sys, "stdin", _TTY())
    monkeypatch.setattr(_cli_core.sys, "stdout", _TTY())
    for command in (
        "start",
        "stop",
        "restart",
        "serve",
        "monitor",
        "dashboard",
        "upgrade",
        "menu",
        "requests",
    ):
        assert _cli_core._update_nudge_allowed(command) is False

    assert _cli_core._update_nudge_suppressed(parser.parse_args(["index", ".", "--watch"]))
    assert _cli_core._update_nudge_suppressed(parser.parse_args(["trigger", "watch"]))


def test_update_choice_runs_existing_update_command(monkeypatch, capsys):
    called = []
    _consented(monkeypatch)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(
        _cli_core,
        "_latest_for_update_nudge",
        lambda: ("999.0.0", {"checked_at": 1.0, "latest": "999.0.0"}),
    )
    monkeypatch.setattr(builtins, "input", lambda prompt: "update")
    monkeypatch.setattr(_cli_core, "cmd_update", lambda args: called.append(args))

    _cli_core._maybe_prompt_for_update("status")

    assert len(called) == 1
    assert called[0].check is False
    assert called[0].dry_run is False
    out = capsys.readouterr().out
    assert "Options: [U]pdate now  [S]kip this version" in out


def test_skip_suppresses_exact_version_but_not_a_newer_release(monkeypatch, tmp_path, capsys):
    path = _cache_at(monkeypatch, tmp_path)
    _consented(monkeypatch)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "skip")
    monkeypatch.setattr(
        _cli_core,
        "_latest_for_update_nudge",
        lambda: ("999.0.0", {"checked_at": 1.0, "latest": "999.0.0"}),
    )

    _cli_core._maybe_prompt_for_update("status")

    state = json.loads(path.read_text())
    assert state["skipped_version"] == "999.0.0"
    assert "Skipped 999.0.0" in capsys.readouterr().out

    def must_not_prompt(prompt):
        raise AssertionError("same skipped version prompted again")

    monkeypatch.setattr(builtins, "input", must_not_prompt)
    monkeypatch.setattr(
        _cli_core,
        "_latest_for_update_nudge",
        lambda: ("999.0.0", state),
    )
    _cli_core._maybe_prompt_for_update("status")

    prompted = []
    monkeypatch.setattr(builtins, "input", lambda prompt: prompted.append(prompt) or "n")
    monkeypatch.setattr(
        _cli_core,
        "_latest_for_update_nudge",
        lambda: ("1000.0.0", state),
    )
    _cli_core._maybe_prompt_for_update("status")
    assert prompted


def test_enter_uses_documented_skip_default(monkeypatch, capsys):
    skipped = []
    prompts = []
    _consented(monkeypatch)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(
        _cli_core,
        "_latest_for_update_nudge",
        lambda: ("999.0.0", {"checked_at": 1.0, "latest": "999.0.0"}),
    )
    monkeypatch.setattr(builtins, "input", lambda prompt: prompts.append(prompt) or "")
    monkeypatch.setattr(_cli_core, "_mark_update_skipped", skipped.append)

    _cli_core._maybe_prompt_for_update("status")

    assert skipped == ["999.0.0"]
    assert prompts == ["    Choose [u/S]: "]
    assert "Skipped 999.0.0" in capsys.readouterr().out


def test_concurrent_failed_probe_cannot_erase_skip(monkeypatch, tmp_path):
    path = _cache_at(monkeypatch, tmp_path / "home")
    assert _cli_core._set_automatic_update_checks(True)
    probe_entered = threading.Event()
    release_probe = threading.Event()

    def fail_after_skip_starts(timeout):
        probe_entered.set()
        assert release_probe.wait(timeout=2)
        raise OSError("offline")

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", fail_after_skip_starts)
    probe = threading.Thread(target=_cli_core._latest_for_update_nudge, kwargs={"now": 100.0})
    skip = threading.Thread(target=_cli_core._mark_update_skipped, args=("999.0.0",))

    probe.start()
    assert probe_entered.wait(timeout=2)
    skip.start()
    release_probe.set()
    probe.join(timeout=2)
    skip.join(timeout=2)

    assert not probe.is_alive()
    assert not skip.is_alive()
    assert json.loads(path.read_text())["skipped_version"] == "999.0.0"


def test_disable_wins_after_stale_consent_read(monkeypatch, tmp_path):
    path = _cache_at(monkeypatch, tmp_path)
    assert _cli_core._set_automatic_update_checks(True)
    consent_read = threading.Event()
    release_stale_caller = threading.Event()
    original_read_consent = _cli_core._automatic_update_checks_enabled

    def stale_consent_read():
        value = original_read_consent()
        consent_read.set()
        assert release_stale_caller.wait(timeout=2)
        return value

    monkeypatch.setattr(_cli_core, "_automatic_update_checks_enabled", stale_consent_read)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)

    def fail(timeout):
        raise AssertionError("stale consent caused a PyPI request after disable completed")

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", fail)
    caller = threading.Thread(target=_cli_core._maybe_prompt_for_update, args=("status",))
    caller.start()
    assert consent_read.wait(timeout=2)
    assert _cli_core._set_automatic_update_checks(False)
    release_stale_caller.set()
    caller.join(timeout=2)

    assert not caller.is_alive()
    assert json.loads(path.read_text())["automatic_checks"] is False


def test_update_cache_lock_excludes_distinct_process(tmp_path):
    cache_path = tmp_path / "update_check.json"
    context = multiprocessing.get_context("spawn")
    holder_entered = context.Event()
    release_holder = context.Event()
    contender_entered = context.Event()
    holder = context.Process(
        target=_hold_update_cache_lock,
        args=(str(cache_path), holder_entered, release_holder),
    )
    contender = context.Process(
        target=_acquire_update_cache_lock,
        args=(str(cache_path), contender_entered),
    )

    holder.start()
    assert holder_entered.wait(timeout=5)
    contender.start()
    assert not contender_entered.wait(timeout=0.25)
    release_holder.set()
    assert contender_entered.wait(timeout=5)
    holder.join(timeout=5)
    contender.join(timeout=5)

    assert holder.exitcode == 0
    assert contender.exitcode == 0


def test_successful_system_exit_still_reaches_update_hook(monkeypatch):
    prompted = []
    args = SimpleNamespace(
        command="status",
        func=lambda unused: (_ for _ in ()).throw(SystemExit(0)),
    )
    parser = type("Parser", (), {"parse_args": lambda self: args})()
    monkeypatch.setattr(_cli_core, "build_parser", lambda: parser)
    monkeypatch.setattr(_cli_core, "_core_command_names", lambda: {"status"})
    monkeypatch.setattr(_cli_core, "_is_first_run", lambda: False)
    monkeypatch.setattr(
        _cli_core, "_maybe_prompt_for_update", lambda *a, **k: prompted.append((a, k))
    )
    monkeypatch.setattr(_cli_core.sys, "argv", ["tokenpak", "status"])

    with pytest.raises(SystemExit) as exc:
        _cli_core.main()

    assert exc.value.code == 0
    assert len(prompted) == 1


def test_optional_probe_failure_preserves_command_output_and_success(monkeypatch, capsys):
    args = SimpleNamespace(command="status", func=lambda unused: print("command-output"))
    parser = type("Parser", (), {"parse_args": lambda self: args})()
    monkeypatch.setattr(_cli_core, "build_parser", lambda: parser)
    monkeypatch.setattr(_cli_core, "_core_command_names", lambda: {"status"})
    monkeypatch.setattr(_cli_core, "_is_first_run", lambda: False)
    monkeypatch.setattr(_cli_core, "_update_nudge_allowed", lambda *a, **k: True)
    monkeypatch.setattr(_cli_core, "_automatic_update_checks_enabled", lambda: True)
    monkeypatch.setattr(
        _cli_core,
        "_latest_for_update_nudge",
        lambda: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(_cli_core.sys, "argv", ["tokenpak", "status"])

    assert _cli_core.main() is None
    assert capsys.readouterr().out == "command-output\n"


def test_optout_and_noninteractive_environment_disable_nudge(monkeypatch):
    monkeypatch.setattr(_cli_core.sys, "stdin", _TTY())
    monkeypatch.setattr(_cli_core.sys, "stdout", _TTY())
    monkeypatch.setenv("TOKENPAK_NO_UPDATE_CHECK", "1")
    assert _cli_core._update_nudge_allowed("status") is False
    monkeypatch.delenv("TOKENPAK_NO_UPDATE_CHECK")
    monkeypatch.setenv("TOKENPAK_NONINTERACTIVE", "1")
    assert _cli_core._update_nudge_allowed("status") is False


def test_update_check_preference_flags_never_touch_network(monkeypatch, tmp_path, capsys):
    path = _cache_at(monkeypatch, tmp_path)
    parser = _cli_core.build_parser()

    def fail(timeout):
        raise AssertionError("changing consent attempted a PyPI request")

    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", fail)
    assert _cli_core.cmd_update(parser.parse_args(["update", "--enable-checks"])) == 0
    assert json.loads(path.read_text())["automatic_checks"] is True

    assert _cli_core.cmd_update(parser.parse_args(["update", "--check-status"])) == 0
    assert "Automatic update checks: enabled" in capsys.readouterr().out

    assert _cli_core.cmd_update(parser.parse_args(["update", "--disable-checks"])) == 0
    assert json.loads(path.read_text())["automatic_checks"] is False


def test_direct_check_does_not_enable_automatic_checks(monkeypatch, tmp_path):
    path = _cache_at(monkeypatch, tmp_path)
    assert _cli_core._set_automatic_update_checks(False)
    monkeypatch.setattr(_cli_core, "_fetch_latest_pypi_version", lambda timeout: "999.0.0")

    args = _cli_core.build_parser().parse_args(["update", "--check"])
    _cli_core.cmd_update(args)

    assert json.loads(path.read_text())["automatic_checks"] is False


def test_windows_cache_lock_path_uses_msvcrt(monkeypatch, tmp_path):
    calls = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=2,
        locking=lambda fd, mode, size: calls.append((fd, mode, size)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    lock_path = tmp_path / "update_check.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _cli_core._lock_update_cache_fd(fd, platform_name="nt")
        _cli_core._unlock_update_cache_fd(fd, platform_name="nt")
    finally:
        os.close(fd)

    assert lock_path.read_bytes() == b"\0"
    assert [mode for _, mode, _ in calls] == [fake_msvcrt.LK_LOCK, fake_msvcrt.LK_UNLCK]
    assert all(size == 1 for _, _, size in calls)


class _MetadataResponse:
    def __init__(self, payload, url=_update_check.PYPI_VERSION_METADATA_URL):
        self._payload = payload
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._url

    def read(self, size=-1):
        assert size == _update_check._MAX_RESPONSE_BYTES + 1
        return json.dumps(self._payload).encode()


def test_core_metadata_request_is_exact_bodyless_and_version_validated(monkeypatch):
    seen = []
    response = _MetadataResponse({"info": {"version": "2.0.0"}})

    class _Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return response

    monkeypatch.setattr(_update_check.urllib.request, "build_opener", lambda *a: _Opener())

    assert _update_check.fetch_latest_pypi_version(timeout=1.5) == "2.0.0"
    request, timeout = seen[0]
    assert request.full_url == _update_check.PYPI_VERSION_METADATA_URL
    assert request.get_method() == "GET"
    assert request.data is None
    assert dict(request.header_items()) == {"Accept": "application/json"}
    assert timeout == 1.5


def test_core_metadata_rejects_redirects_and_invalid_versions(monkeypatch):
    class _Opener:
        response = _MetadataResponse(
            {"info": {"version": "2.0.0"}},
            url="https://example.invalid/tokenpak.json",
        )

        def open(self, request, timeout):
            return self.response

    opener = _Opener()
    monkeypatch.setattr(_update_check.urllib.request, "build_opener", lambda *a: opener)
    with pytest.raises(ValueError, match="response URL changed"):
        _update_check.fetch_latest_pypi_version()

    opener.response = _MetadataResponse({"info": {"version": "not a version"}})
    with pytest.raises(ValueError, match="invalid version"):
        _update_check.fetch_latest_pypi_version()


def test_core_redirect_handler_fails_closed():
    request = _update_check.urllib.request.Request(
        _update_check.PYPI_VERSION_METADATA_URL,
        method="GET",
    )
    with pytest.raises(_update_check.urllib.error.HTTPError):
        _update_check._RejectRedirects().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.invalid/tokenpak.json",
        )
