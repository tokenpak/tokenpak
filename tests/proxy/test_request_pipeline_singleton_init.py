"""Lazy-singleton init-failure handling in the request pipeline.

When a guardrail singleton (BudgetController / RouteEngine / PreconditionGates)
fails to construct, the proxy degrades gracefully (keeps serving) — but the
failure must be LOGGED exactly once at WARNING and must NOT be retried on every
subsequent request (which would both spam logs and silently keep spend/budget
enforcement disabled without any signal).
"""

from __future__ import annotations

import logging

import tokenpak.proxy.request_pipeline as rp
import tokenpak.telemetry.budget_controller as bc_mod


def test_budget_controller_init_failure_logged_once_and_not_retried(monkeypatch, caplog):
    # Start from a clean slot regardless of prior tests.
    monkeypatch.setattr(rp, "_BUDGET_CTRL_INSTANCE", None)

    calls = {"n": 0}

    class _Boom:
        def __init__(self, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("simulated BudgetController init failure")

    monkeypatch.setattr(bc_mod, "BudgetController", _Boom)

    with caplog.at_level(logging.WARNING, logger="tokenpak.proxy.request_pipeline"):
        first = rp._get_budget_controller()
        second = rp._get_budget_controller()
        third = rp._get_budget_controller()

    # Graceful degrade: callers get None, not an exception.
    assert first is None
    assert second is None
    assert third is None

    # Construction attempted exactly once — the failure sentinel prevents
    # per-request retries.
    assert calls["n"] == 1
    assert rp._BUDGET_CTRL_INSTANCE is rp._INIT_FAILED

    # Logged exactly once at WARNING (with traceback context via exc_info).
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "BudgetController init failed" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
