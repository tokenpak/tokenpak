"""Regression tests for tokenpak.telemetry.install_reporter's heartbeat loop.

Covers a dead cross-subsystem import that used to disable the install
heartbeat forever, silently: the loop imported a metrics-enabled check from
a module path that no longer existed, and a bare ``except Exception`` around
the import swallowed the resulting error on every single iteration with zero
signal. These tests assert:

- The metrics-enabled check the loop actually imports at runtime
  (``tokenpak.core.config.get_metrics_enabled``) is real, importable, and
  callable — the simplest possible guard against the same class of drift
  (rename/move without updating this call site).
- If that import (or the call itself) breaks again, the loop now logs the
  failure loudly (ERROR) instead of disappearing into a generic retry-loop
  ``except Exception``.
- The loop still never raises out of a broken check — it keeps its
  forgiving, non-crashing retry behavior either way.
"""

from __future__ import annotations

import logging

import pytest


class _StopLoop(Exception):
    """Sentinel raised from a patched time.sleep() to end the infinite loop
    after exactly one iteration, so the loop body can be exercised directly.
    """


def test_get_metrics_enabled_is_importable_and_callable():
    """Regression test for the dead import: the heartbeat loop's
    `from tokenpak.core.config import get_metrics_enabled` must resolve to a
    real, callable function returning a bool.
    """
    from tokenpak.core.config import get_metrics_enabled

    assert callable(get_metrics_enabled)
    assert isinstance(get_metrics_enabled(), bool)


def test_heartbeat_loop_happy_path_emits_no_error_log(monkeypatch, caplog):
    """Normal operation (metrics disabled) must not trip the new loud-error
    path — only genuine import/attribute drift should.
    """
    import tokenpak.core.config as core_config
    from tokenpak.telemetry import install_reporter

    monkeypatch.setattr(core_config, "get_metrics_enabled", lambda: False)

    def _sleep_once(_interval):
        raise _StopLoop()

    monkeypatch.setattr(install_reporter.time, "sleep", _sleep_once)

    with caplog.at_level(logging.DEBUG, logger=install_reporter.logger.name):
        with pytest.raises(_StopLoop):
            install_reporter._heartbeat_loop("http://example.invalid", 1)

    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_heartbeat_loop_logs_error_when_metrics_enabled_missing(monkeypatch, caplog):
    """Reproduce the exact historical bug shape: the name the loop imports
    (get_metrics_enabled) no longer exists on tokenpak.core.config, e.g.
    after a future rename/move. The loop must log loudly and keep running,
    not silently retry forever.
    """
    import tokenpak.core.config as core_config
    from tokenpak.telemetry import install_reporter

    monkeypatch.delattr(core_config, "get_metrics_enabled")

    def _sleep_once(_interval):
        raise _StopLoop()

    monkeypatch.setattr(install_reporter.time, "sleep", _sleep_once)

    with caplog.at_level(logging.DEBUG, logger=install_reporter.logger.name):
        with pytest.raises(_StopLoop):
            install_reporter._heartbeat_loop("http://example.invalid", 1)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "a broken get_metrics_enabled import must be logged loudly"
    assert "get_metrics_enabled" in error_records[0].getMessage()


def test_heartbeat_loop_logs_error_when_metrics_enabled_call_broken(monkeypatch, caplog):
    """Same class of failure, one step later: the import succeeds but the
    callable itself is broken (e.g. a signature/behavior change upstream).
    """
    import tokenpak.core.config as core_config
    from tokenpak.telemetry import install_reporter

    def _broken(*_args, **_kwargs):
        raise AttributeError("get_metrics_enabled has been renamed")

    monkeypatch.setattr(core_config, "get_metrics_enabled", _broken)

    def _sleep_once(_interval):
        raise _StopLoop()

    monkeypatch.setattr(install_reporter.time, "sleep", _sleep_once)

    with caplog.at_level(logging.DEBUG, logger=install_reporter.logger.name):
        with pytest.raises(_StopLoop):
            install_reporter._heartbeat_loop("http://example.invalid", 1)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "a broken get_metrics_enabled call must be logged loudly"
