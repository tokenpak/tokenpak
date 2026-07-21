"""Focused MemoryGuard lifecycle, threshold, and platform regressions."""

from __future__ import annotations

import signal
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import tokenpak.proxy.memory_guard as memory_guard_module
import tokenpak.proxy.server as server_module
from tokenpak.proxy.memory_guard import MemoryGuard, MemoryMeasurementUnsupported
from tokenpak.proxy.server import ProxyServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _configure_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "1")
    monkeypatch.setenv("TOKENPAK_MEMORY_TARGET_MB", "99999")
    monkeypatch.setenv("TOKENPAK_MEMORY_CEILING_MB", "100999")
    monkeypatch.setenv("TOKENPAK_MEMORY_CHECK_SECS", "0.01")
    monkeypatch.setenv("TOKENPAK_MEMORY_COOLDOWN_SECS", "0.05")
    monkeypatch.setenv("TOKENPAK_MEMORY_SYS_LOW_MB", "0")


def _make_proxy(monkeypatch: pytest.MonkeyPatch, port: int) -> ProxyServer:
    monkeypatch.setattr(server_module, "_DbMonitor", lambda _path: None)
    monkeypatch.setattr(server_module, "run_startup_checks", lambda _port: (True, []))
    proxy = ProxyServer(host="127.0.0.1", port=port, shutdown_timeout=0.2)
    proxy._flush_telemetry = lambda: None  # type: ignore[method-assign]
    return proxy


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def test_proxy_lifecycle_starts_and_joins_exactly_one_guard(monkeypatch):
    _configure_guard(monkeypatch)
    port = _free_port()
    proxy = _make_proxy(monkeypatch, port)
    guard_threads_before = {
        thread.ident for thread in threading.enumerate() if thread.name == "tokenpak-memory-guard"
    }

    proxy.start(blocking=False)
    proxy_thread = proxy._server_thread
    try:
        _wait_until(lambda: proxy._memory_guard_snapshot()["checks"] >= 1)
        snapshot = proxy._memory_guard_snapshot()
        live_guard_threads = [
            thread
            for thread in threading.enumerate()
            if thread.name == "tokenpak-memory-guard" and thread.ident not in guard_threads_before
        ]
        assert snapshot["enabled"] is True
        assert snapshot["state"] == "running"
        assert snapshot["thread_alive"] is True
        assert len(live_guard_threads) == 1
        assert snapshot["thread_ident"] == live_guard_threads[0].ident
        assert snapshot["callbacks"] == {
            "compact": False,
            "token": False,
            "semantic": False,
        }
        assert snapshot["callback_policy"] == ("gc_trim_only_no_unbounded_disposable_proxy_cache")
    finally:
        proxy.stop()

    stopped = proxy._memory_guard_snapshot()
    assert stopped["state"] == "stopped"
    assert stopped["thread_alive"] is False
    assert proxy._server_thread is None
    assert proxy._lifecycle_state == "stopped"
    assert proxy_thread is not None and not proxy_thread.is_alive()
    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name == "tokenpak-memory-guard" and thread.ident not in guard_threads_before
    ]

    # The listener handle is released and repeated stop remains idempotent.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
        rebound.bind(("127.0.0.1", port))
    proxy.stop()
    assert proxy._memory_guard_snapshot()["state"] == "stopped"
    with pytest.raises(RuntimeError, match="single-use"):
        proxy.start(blocking=False)


def test_disabled_proxy_has_no_guard_thread(monkeypatch):
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    proxy = _make_proxy(monkeypatch, _free_port())
    snapshot = proxy._memory_guard_snapshot()
    assert {key: value for key, value in snapshot.items() if key != "configuration"} == {
        "enabled": False,
        "state": "disabled",
        "thread_alive": False,
        "callback_policy": "disabled",
        "callbacks": {"compact": False, "token": False, "semantic": False},
    }
    assert snapshot["configuration"]["source"] == "environment"
    assert snapshot["configuration"]["mode"] == "off"
    assert snapshot["configuration"]["triggering_env"] == ["TOKENPAK_MEMORY_GUARD"]
    proxy.stop()
    assert proxy._lifecycle_state == "stopped"


def test_corrupt_managed_config_fails_off_and_surfaces_health_warning(monkeypatch, tmp_path):
    for name in memory_guard_module._MEMORY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    (tmp_path / "memory-optimization.json").write_text("truncated")

    proxy = _make_proxy(monkeypatch, _free_port())
    snapshot = proxy._memory_guard_snapshot()
    health = proxy.health()

    assert snapshot["enabled"] is False
    assert snapshot["configuration"]["source"] == "managed_error"
    assert "ignored" in snapshot["configuration"]["warning"]
    assert health["memory_guard"]["configuration"] == snapshot["configuration"]
    assert health["status"] == "ok"
    proxy.stop()


def test_stop_before_start_is_terminal(monkeypatch):
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    proxy = _make_proxy(monkeypatch, _free_port())

    proxy.stop()

    assert proxy._lifecycle_state == "stopped"
    with pytest.raises(RuntimeError, match="single-use"):
        proxy.start(blocking=False)


def test_signal_stop_clears_owned_thread_handles(monkeypatch):
    _configure_guard(monkeypatch)
    proxy = _make_proxy(monkeypatch, _free_port())
    proxy.start(blocking=False)

    proxy._handle_signal(signal.SIGTERM, None)
    _wait_until(
        lambda: proxy._server is None and proxy._signal_stop_thread is None,
        timeout=3,
    )

    assert proxy._server_thread is None
    assert proxy._lifecycle_state == "stopped"
    assert proxy._memory_guard_snapshot()["state"] == "stopped"
    assert proxy._memory_guard_snapshot()["thread_alive"] is False


def test_guard_start_failure_releases_listener(monkeypatch):
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    port = _free_port()
    proxy = _make_proxy(monkeypatch, port)
    guard = MagicMock()
    guard.start.side_effect = MemoryMeasurementUnsupported("no trustworthy metric")
    proxy._memory_guard = guard

    with pytest.raises(MemoryMeasurementUnsupported, match="trustworthy"):
        proxy.start(blocking=False)

    guard.stop.assert_called_once_with()
    assert proxy._lifecycle_state == "start_failed"
    assert proxy._server is None
    assert proxy._server_thread is None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
        rebound.bind(("127.0.0.1", port))
    proxy.stop()
    assert proxy._lifecycle_state == "stopped"


def test_server_cleanup_failure_retains_owned_handle_for_retry(monkeypatch):
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    proxy = _make_proxy(monkeypatch, _free_port())
    owned_server = MagicMock()
    owned_server.shutdown.side_effect = RuntimeError("shutdown failed")
    proxy._server = owned_server

    with pytest.raises(RuntimeError, match="shutdown failed"):
        proxy.stop()

    assert proxy._server is owned_server
    assert proxy._lifecycle_state == "stop_failed"
    owned_server.shutdown.side_effect = None
    proxy.stop()
    assert proxy._server is None
    assert proxy._lifecycle_state == "stopped"


def test_failed_start_close_retains_listener_for_direct_retry(monkeypatch):
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    proxy = _make_proxy(monkeypatch, _free_port())
    guard = MagicMock()
    guard.start.side_effect = RuntimeError("guard start failed")
    proxy._memory_guard = guard
    owned_server = MagicMock()
    owned_server.server_close.side_effect = RuntimeError("close failed")
    monkeypatch.setattr(server_module, "_ThreadedHTTPServer", lambda *_args: owned_server)

    with pytest.raises(RuntimeError, match="guard start failed"):
        proxy.start(blocking=False)

    assert proxy._server is owned_server
    assert proxy._lifecycle_state == "start_cleanup_failed"
    owned_server.server_close.side_effect = None
    proxy.stop()
    owned_server.shutdown.assert_not_called()
    assert proxy._server is None
    assert proxy._lifecycle_state == "stopped"


def test_blocking_signal_install_failure_owns_no_listener_or_thread(monkeypatch):
    _configure_guard(monkeypatch)
    proxy = _make_proxy(monkeypatch, _free_port())
    monkeypatch.setattr(
        server_module.signal,
        "signal",
        MagicMock(side_effect=ValueError("signal install failed")),
    )

    with pytest.raises(ValueError, match="signal install failed"):
        proxy.start(blocking=True)

    assert proxy._server is None
    assert proxy._server_thread is None
    assert proxy._memory_guard_snapshot()["state"] == "created"
    assert proxy._memory_guard_snapshot()["thread_alive"] is False
    assert proxy._lifecycle_state == "start_failed"
    proxy.stop()


def test_stop_waits_for_startup_gate_then_disposes_started_server(monkeypatch):
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    proxy = _make_proxy(monkeypatch, _free_port())
    checks_entered = threading.Event()
    release_checks = threading.Event()
    errors: list[Exception] = []

    def blocked_checks(_port):
        checks_entered.set()
        assert release_checks.wait(timeout=2)
        return True, []

    monkeypatch.setattr(server_module, "run_startup_checks", blocked_checks)

    def run_start() -> None:
        try:
            proxy.start(blocking=False)
        except Exception as exc:  # pragma: no cover - asserted empty below
            errors.append(exc)

    starter = threading.Thread(target=run_start)
    stopper = threading.Thread(target=proxy.stop)
    starter.start()
    assert checks_entered.wait(timeout=1)
    stopper.start()
    time.sleep(0.05)
    assert stopper.is_alive(), "stop must wait for the lock-protected startup gate"
    release_checks.set()
    starter.join(timeout=2)
    stopper.join(timeout=3)

    assert errors == []
    assert not starter.is_alive()
    assert not stopper.is_alive()
    assert proxy._lifecycle_state == "stopped"
    assert proxy._server is None


def test_threshold_boundaries_cooldown_and_red_escalation(monkeypatch):
    rss_samples = iter([90, 100, 99, 101, 200, 190])
    monkeypatch.setattr(memory_guard_module, "get_rss_mb", lambda: next(rss_samples))
    monkeypatch.setattr(memory_guard_module, "get_available_ram_mb", lambda: 1000)
    monkeypatch.setattr(memory_guard_module.gc, "collect", lambda: 0)
    monkeypatch.setattr(memory_guard_module, "malloc_trim", lambda: False)
    compact = MagicMock(return_value=3)
    guard = MemoryGuard(
        target_mb=100,
        ceiling_mb=200,
        check_interval_secs=1,
        cooldown_secs=10,
        on_evict_compact_cache=compact,
    )

    guard._check(now=0)  # below target: GREEN
    guard._check(now=1)  # target is inclusive: one YELLOW action
    guard._check(now=2)  # same-level repeat is suppressed by cooldown
    guard._check(now=3)  # ceiling is inclusive: RED escalation is immediate

    stats = guard.stats
    assert stats["checks"] == 4
    assert stats["yellow_triggers"] == 1
    assert stats["red_triggers"] == 1
    assert stats["suppressed_actions"] == 1
    assert stats["compact_evictions"] == 6
    assert compact.call_args_list[0].args == (25,)
    assert compact.call_args_list[1].args == (50,)
    assert stats["config"]["hysteresis_mb"] == 25


def test_observe_mode_records_pressure_without_taking_action(monkeypatch):
    monkeypatch.setattr(memory_guard_module, "get_rss_mb", lambda: 250)
    monkeypatch.setattr(memory_guard_module, "get_available_ram_mb", lambda: 1000)
    collect = MagicMock(return_value=0)
    trim = MagicMock(return_value=False)
    evict = MagicMock(return_value=1)
    monkeypatch.setattr(memory_guard_module.gc, "collect", collect)
    monkeypatch.setattr(memory_guard_module, "malloc_trim", trim)
    guard = MemoryGuard(
        target_mb=100,
        ceiling_mb=200,
        action_mode="observe",
        on_evict_compact_cache=evict,
    )

    guard._check(now=0)

    snapshot = guard.stats
    assert snapshot["last_level"] == "RED"
    assert snapshot["observed_pressure_checks"] == 1
    assert snapshot["red_triggers"] == 0
    assert snapshot["config"]["action_mode"] == "observe"
    collect.assert_not_called()
    trim.assert_not_called()
    evict.assert_not_called()


def test_hysteresis_exposes_recovery_band_before_green(monkeypatch):
    rss_samples = iter([100, 99, 90, 75])
    monkeypatch.setattr(memory_guard_module, "get_rss_mb", lambda: next(rss_samples))
    monkeypatch.setattr(memory_guard_module, "get_available_ram_mb", lambda: 1000)
    monkeypatch.setattr(memory_guard_module.gc, "collect", lambda: 0)
    monkeypatch.setattr(memory_guard_module, "malloc_trim", lambda: False)
    guard = MemoryGuard(
        target_mb=100,
        ceiling_mb=200,
        check_interval_secs=1,
        cooldown_secs=10,
    )

    guard._check(now=0)
    assert guard.stats["pressure_latched"] is True
    guard._check(now=1)
    assert guard.stats["last_level"] == "RECOVERY"
    assert guard.stats["pressure_latched"] is True
    guard._check(now=2)
    assert guard.stats["last_level"] == "GREEN"
    assert guard.stats["pressure_latched"] is False


def test_hysteresis_is_capped_below_low_target():
    guard = MemoryGuard(target_mb=10, ceiling_mb=1000)
    assert guard.hysteresis_mb == 9
    assert guard.target_mb - guard.hysteresis_mb == 1


def test_sys_low_exact_boundary_and_recovery(monkeypatch):
    rss_samples = iter([50, 50, 50, 50, 50])
    available_samples = iter([50, 49, 60, 75])
    monkeypatch.setattr(memory_guard_module, "get_rss_mb", lambda: next(rss_samples))
    monkeypatch.setattr(
        memory_guard_module,
        "get_available_ram_mb",
        lambda: next(available_samples),
    )
    monkeypatch.setattr(memory_guard_module.gc, "collect", lambda: 0)
    monkeypatch.setattr(memory_guard_module, "malloc_trim", lambda: False)
    guard = MemoryGuard(
        target_mb=100,
        ceiling_mb=200,
        sys_low_mb=50,
        check_interval_secs=1,
        cooldown_secs=10,
    )

    guard._check(now=0)
    assert guard.stats["last_level"] == "GREEN"
    guard._check(now=1)
    assert guard.stats["sys_low_triggers"] == 1
    guard._check(now=2)
    assert guard.stats["last_level"] == "RECOVERY"
    guard._check(now=3)
    assert guard.stats["last_level"] == "GREEN"
    assert guard.stats["pressure_latched"] is False


def test_post_start_measurement_failure_degrades_proxy_health(monkeypatch):
    _configure_guard(monkeypatch)
    calls = 0

    def failing_rss() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:  # synchronous start probe
            return 50
        raise MemoryMeasurementUnsupported("measurement disappeared")

    monkeypatch.setattr(memory_guard_module, "get_rss_mb", failing_rss)
    monkeypatch.setattr(memory_guard_module, "get_available_ram_mb", lambda: 1000)
    proxy = _make_proxy(monkeypatch, _free_port())
    proxy.start(blocking=False)
    try:
        _wait_until(lambda: proxy._memory_guard_snapshot()["state"] == "degraded")
        health = proxy.health()
        assert health["status"] == "degraded"
        assert health["is_degraded"] is True
        assert health["memory_guard"]["measurement"]["supported"] is False
        assert "measurement disappeared" in health["memory_guard"]["last_error"]
    finally:
        proxy.stop()


def test_blocked_callback_retains_handle_until_clean_stop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked_callback(_percent: int) -> int:
        entered.set()
        release.wait(timeout=2)
        return 1

    monkeypatch.setattr(memory_guard_module, "get_rss_mb", lambda: 150)
    monkeypatch.setattr(memory_guard_module, "get_available_ram_mb", lambda: 1000)
    monkeypatch.setattr(memory_guard_module.gc, "collect", lambda: 0)
    monkeypatch.setattr(memory_guard_module, "malloc_trim", lambda: False)
    guard = MemoryGuard(
        target_mb=100,
        ceiling_mb=200,
        check_interval_secs=0.01,
        cooldown_secs=0.05,
        on_evict_compact_cache=blocked_callback,
    )

    guard.start()
    assert entered.wait(timeout=1)
    with pytest.raises(RuntimeError, match="did not stop"):
        guard.stop(timeout=0.01)
    assert guard.stats["state"] == "stop_timeout"
    assert guard.stats["thread_alive"] is True

    release.set()
    guard.stop(timeout=1)
    assert guard.stats["state"] == "stopped"
    assert guard.stats["thread_alive"] is False


def test_callback_can_read_stats_while_stop_joins(monkeypatch):
    entered = threading.Event()
    read_status = threading.Event()
    callback_done = threading.Event()
    stop_errors: list[Exception] = []
    guard: MemoryGuard

    def callback(_percent: int) -> int:
        entered.set()
        assert read_status.wait(timeout=1)
        assert guard.stats["stopping"] is True
        callback_done.set()
        return 1

    monkeypatch.setattr(memory_guard_module, "get_rss_mb", lambda: 150)
    monkeypatch.setattr(memory_guard_module, "get_available_ram_mb", lambda: 1000)
    monkeypatch.setattr(memory_guard_module.gc, "collect", lambda: 0)
    monkeypatch.setattr(memory_guard_module, "malloc_trim", lambda: False)
    guard = MemoryGuard(
        target_mb=100,
        ceiling_mb=200,
        check_interval_secs=0.01,
        cooldown_secs=0.05,
        on_evict_compact_cache=callback,
    )
    guard.start()
    assert entered.wait(timeout=1)

    def stop_guard() -> None:
        try:
            guard.stop(timeout=1)
        except Exception as exc:  # pragma: no cover - asserted empty below
            stop_errors.append(exc)

    stopper = threading.Thread(target=stop_guard)
    stopper.start()
    _wait_until(lambda: guard.stats["stopping"] is True)
    read_status.set()
    stopper.join(timeout=2)

    assert callback_done.is_set()
    assert not stopper.is_alive()
    assert stop_errors == []
    assert guard.stats["state"] == "stopped"


@pytest.mark.parametrize(
    ("interval", "cooldown"),
    [(float("nan"), 300), (float("inf"), 300), (1, float("nan")), (1, float("inf"))],
)
def test_non_finite_cadence_is_rejected(interval, cooldown):
    with pytest.raises(ValueError):
        MemoryGuard(
            target_mb=100,
            ceiling_mb=200,
            check_interval_secs=interval,
            cooldown_secs=cooldown,
        )


@pytest.mark.parametrize(("platform", "expected"), [("darwin", "macos"), ("win32", "windows")])
def test_psutil_measurement_paths(platform, expected, monkeypatch):
    virtual = SimpleNamespace(total=8 * 1024 * 1024, available=3 * 1024 * 1024)
    process = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=2 * 1024 * 1024))
    fake_psutil = SimpleNamespace(
        virtual_memory=lambda: virtual,
        Process=lambda: process,
    )
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(memory_guard_module, "_load_psutil", lambda: fake_psutil)

    assert memory_guard_module.memory_measurement_support() == {
        "supported": True,
        "platform": expected,
        "source": "psutil",
        "reason": None,
    }
    assert memory_guard_module.get_total_ram_mb() == 8
    assert memory_guard_module.get_available_ram_mb() == 3
    assert memory_guard_module.get_rss_mb() == 2


def test_psutil_missing_is_explicit_unsupported(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(memory_guard_module, "_load_psutil", lambda: None)
    guard = MemoryGuard(target_mb=100, ceiling_mb=200)

    with pytest.raises(MemoryMeasurementUnsupported, match="psutil is required"):
        guard.start()

    assert guard.stats["state"] == "unsupported"
    assert guard.stats["measurement"]["supported"] is False
    assert guard.stats["thread_alive"] is False


def test_psutil_read_failure_is_explicit_unsupported(monkeypatch):
    process = SimpleNamespace(memory_info=MagicMock(side_effect=OSError("process read failed")))
    fake_psutil = SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(total=8 * 1024 * 1024, available=1),
        Process=lambda: process,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(memory_guard_module, "_load_psutil", lambda: fake_psutil)
    guard = MemoryGuard(target_mb=100, ceiling_mb=200)

    with pytest.raises(MemoryMeasurementUnsupported, match="psutil RSS read failed"):
        guard.start()

    snapshot = guard.stats
    assert snapshot["state"] == "unsupported"
    assert snapshot["measurement"]["supported"] is False
    assert "process read failed" in snapshot["measurement"]["reason"]


def test_stats_snapshot_does_not_expose_mutable_measurement_state():
    guard = MemoryGuard(target_mb=100, ceiling_mb=200)
    snapshot = guard.stats
    snapshot["measurement"]["reason"] = "tampered"

    assert guard.stats["measurement"]["reason"] != "tampered"


def test_unsupported_platform_is_explicit_and_starts_no_thread(monkeypatch):
    monkeypatch.setattr(sys, "platform", "unsupported-test-os")
    guard = MemoryGuard(target_mb=100, ceiling_mb=200)

    with pytest.raises(MemoryMeasurementUnsupported, match="unsupported platform"):
        guard.start()

    snapshot = guard.stats
    assert snapshot["state"] == "unsupported"
    assert snapshot["measurement"]["supported"] is False
    assert snapshot["thread_alive"] is False
