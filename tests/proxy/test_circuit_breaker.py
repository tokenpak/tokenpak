"""
tests/proxy/test_circuit_breaker.py

Regression test for TRIX-MTC-07 Fix #2:
  CircuitBreakerRegistry.reload_config() must propagate new config to all
  existing breakers under the registry lock, preventing races.
"""

from __future__ import annotations

import os
import threading
from typing import List

import pytest

from tokenpak.proxy.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    _reset_registry_for_testing,
)


# ---------------------------------------------------------------------------
# Regression test: reload_config() exists and propagates under lock
# ---------------------------------------------------------------------------

def test_circuit_breaker_registry_reload_config_propagates():
    """
    reload_config() must update self._config AND each existing breaker's _config.
    """
    # Start with threshold=5
    cfg = CircuitBreakerConfig(enabled=True, failure_threshold=5, recovery_timeout=60)
    registry = CircuitBreakerRegistry(config=cfg)

    # Create a breaker so it exists before reload
    _ = registry._get_or_create("anthropic")
    assert registry._breakers["anthropic"]._config.failure_threshold == 5

    # Simulate an env-var change and reload
    env_overrides = {
        "TOKENPAK_CB_ENABLED": "1",
        "TOKENPAK_CB_FAILURE_THRESHOLD": "10",
        "TOKENPAK_CB_RECOVERY_TIMEOUT": "120",
        "TOKENPAK_CB_WINDOW_SECONDS": "60",
    }
    original_env = {k: os.environ.get(k) for k in env_overrides}
    try:
        os.environ.update(env_overrides)
        registry.reload_config()
    finally:
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Registry and existing breaker both see the new threshold
    assert registry._config.failure_threshold == 10
    assert registry._breakers["anthropic"]._config.failure_threshold == 10


def test_circuit_breaker_registry_reload_config_concurrent_no_error():
    """
    Concurrent allow_request() + reload_config() must not raise or deadlock.
    """
    errors: List[Exception] = []
    registry = CircuitBreakerRegistry(
        config=CircuitBreakerConfig(enabled=True, failure_threshold=5, recovery_timeout=30)
    )
    # Pre-create two breakers
    registry._get_or_create("openai")
    registry._get_or_create("google")

    stop = threading.Event()

    def checker():
        try:
            for _ in range(300):
                registry.allow_request("openai")
                registry.allow_request("google")
        except Exception as exc:
            errors.append(exc)
        finally:
            stop.set()

    def reloader():
        while not stop.is_set():
            registry.reload_config()

    t_check = threading.Thread(target=checker, daemon=True)
    t_reload = threading.Thread(target=reloader, daemon=True)
    t_reload.start()
    t_check.start()
    t_check.join(timeout=5)
    stop.set()
    t_reload.join(timeout=2)

    assert not errors, f"Concurrent reload raised: {errors}"
