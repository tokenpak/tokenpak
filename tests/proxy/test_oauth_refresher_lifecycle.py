"""ProxyServer wiring for the native-OAuth refresher / cooldown auto-clear.

Backbone audit finding 4.5-2: ``BackgroundOAuthRefresher`` (and
``BackgroundCooldownClearer``) are asyncio-native single-owner components
that ``server_async.py``'s ASGI lifespan already starts and stops, but the
CLI's actual proxy-start path instantiates the thread-based ``ProxyServer``
class, which never started them. These tests pin the fix: ``ProxyServer``
must host both on a dedicated background loop/thread, start them once the
listener is up, and stop them cleanly on shutdown — without introducing a
second refresh path.

This is a lifecycle test, not a live-refresh integration test: no code path
today writes ``auth-profiles.json``, so ``get_expiring_profiles()`` always
returns empty and no real token traffic is exercised here.
"""

from __future__ import annotations

import time

import pytest

import tokenpak.proxy.server as server_module
from tests.proxy._proxy_subprocess import free_port
from tokenpak.core.auth.oauth_manager import BackgroundOAuthRefresher
from tokenpak.core.cooldown import BackgroundCooldownClearer
from tokenpak.proxy.server import ProxyServer


def _make_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path, port: int, **kwargs) -> ProxyServer:
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
    monkeypatch.setattr(server_module, "_DbMonitor", lambda _path: None)
    monkeypatch.setattr(server_module, "run_startup_checks", lambda _port: (True, []))
    proxy = ProxyServer(host="127.0.0.1", port=port, shutdown_timeout=0.2, **kwargs)
    proxy._flush_telemetry = lambda: None  # type: ignore[method-assign]
    return proxy


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def test_proxy_construction_wires_both_background_tasks(monkeypatch, tmp_path):
    """Default config enables both components — same defaults as server_async.py."""
    monkeypatch.setattr(
        "tokenpak.core.config.get_config",
        lambda: {"auth": {}},
    )
    proxy = _make_proxy(monkeypatch, tmp_path, free_port())

    tasks = proxy._oauth_background_tasks
    assert len(tasks) == 2
    assert any(isinstance(t, BackgroundOAuthRefresher) for t in tasks)
    assert any(isinstance(t, BackgroundCooldownClearer) for t in tasks)
    assert proxy._oauth_refresher_snapshot() == {
        "enabled": True,
        "task_count": 2,
        "thread_alive": False,
    }
    proxy.stop()


def test_proxy_start_stop_cycles_the_oauth_refresher_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tokenpak.core.config.get_config",
        lambda: {"auth": {}},
    )
    proxy = _make_proxy(monkeypatch, tmp_path, free_port())

    proxy.start(blocking=False)
    try:
        _wait_until(lambda: proxy._oauth_refresher_snapshot()["thread_alive"] is True)
        snapshot = proxy._oauth_refresher_snapshot()
        assert snapshot["enabled"] is True
        assert snapshot["task_count"] == 2
        # Both single-owner components actually got started (idempotent task
        # handles exist), not just a live thread wrapper around nothing.
        oauth_task, cooldown_task = (
            next(
                t for t in proxy._oauth_background_tasks if isinstance(t, BackgroundOAuthRefresher)
            ),
            next(
                t for t in proxy._oauth_background_tasks if isinstance(t, BackgroundCooldownClearer)
            ),
        )
        assert oauth_task._task is not None
        assert cooldown_task._task is not None
    finally:
        proxy.stop()

    stopped = proxy._oauth_refresher_snapshot()
    assert stopped["thread_alive"] is False
    assert proxy._lifecycle_state == "stopped"

    # Repeated stop stays idempotent — no error, no resurrected thread.
    proxy.stop()
    assert proxy._oauth_refresher_snapshot()["thread_alive"] is False


def test_oauth_auto_refresh_disabled_leaves_only_cooldown_clearer(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tokenpak.core.config.get_config",
        lambda: {"auth": {"oauth_auto_refresh": False}},
    )
    proxy = _make_proxy(monkeypatch, tmp_path, free_port())

    tasks = proxy._oauth_background_tasks
    assert len(tasks) == 1
    assert isinstance(tasks[0], BackgroundCooldownClearer)

    proxy.start(blocking=False)
    try:
        _wait_until(lambda: proxy._oauth_refresher_snapshot()["thread_alive"] is True)
    finally:
        proxy.stop()
    assert proxy._oauth_refresher_snapshot()["thread_alive"] is False


def test_both_background_tasks_disabled_starts_no_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tokenpak.core.config.get_config",
        lambda: {"auth": {"oauth_auto_refresh": False, "auto_clear_cooldowns": False}},
    )
    proxy = _make_proxy(monkeypatch, tmp_path, free_port())

    assert proxy._oauth_background_tasks == []
    assert proxy._oauth_refresher_snapshot() == {
        "enabled": False,
        "task_count": 0,
        "thread_alive": False,
    }

    proxy.start(blocking=False)
    try:
        assert proxy._oauth_refresher_thread.thread_alive is False
    finally:
        proxy.stop()
    assert proxy._oauth_refresher_snapshot()["thread_alive"] is False


def test_background_task_start_failure_does_not_block_listener(monkeypatch, tmp_path):
    """Unlike the memory guard, this is best-effort: a start failure must
    never roll back an already-bound listener (server_async.py's lifespan
    treats a background-task startup failure the same way — log and
    continue)."""
    monkeypatch.setattr(
        "tokenpak.core.config.get_config",
        lambda: {"auth": {}},
    )
    proxy = _make_proxy(monkeypatch, tmp_path, free_port())
    monkeypatch.setattr(
        proxy._oauth_refresher_thread,
        "start",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    proxy.start(blocking=False)
    try:
        assert proxy.is_running()
        assert proxy._lifecycle_state == "running"
    finally:
        proxy.stop()
