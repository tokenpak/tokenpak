"""
tests/proxy/test_cost.py

Regression test for TRIX-MTC-07 Fix #4:
  When the cost tracker raises an exception, record_proxy_request() must log at
  ERROR level with a structured message containing model name and token count.

Before the fix, the failure was logged at WARNING level with no structured
context, leaving no audit trail for ops.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import tokenpak.proxy.proxy as cost_mod

# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


def test_cost_tracking_failure_logs_at_error_with_model_and_tokens(caplog):
    """
    Direct integration: when get_cost_tracker().record_request() raises,
    record_proxy_request() must:
      - return 0.0
      - emit an ERROR-level log containing COST_TRACKING_FAILURE, model, total tokens
    """
    mock_tracker = MagicMock()
    mock_tracker.record_request.side_effect = RuntimeError("disk full")

    orig_enabled = cost_mod._COST_TRACKING_ENABLED
    cost_mod._COST_TRACKING_ENABLED = True
    try:
        with caplog.at_level(logging.ERROR, logger="tokenpak.proxy.proxy"):
            with patch(
                "tokenpak.telemetry.cost_tracker.get_cost_tracker",
                return_value=mock_tracker,
            ):
                result = cost_mod.record_proxy_request(
                    model="claude-opus-4-5",
                    prompt_tokens=200,
                    completion_tokens=75,
                )
    finally:
        cost_mod._COST_TRACKING_ENABLED = orig_enabled

    assert result == 0.0, "Must degrade gracefully and return 0.0"

    error_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("COST_TRACKING_FAILURE" in m for m in error_msgs), (
        f"No COST_TRACKING_FAILURE ERROR log found; got: {error_msgs}"
    )
    assert any("claude-opus-4-5" in m for m in error_msgs), (
        f"Model name missing from error log; got: {error_msgs}"
    )
    # 200 prompt + 75 completion = 275 total tokens
    assert any("275" in m for m in error_msgs), (
        f"Combined token count (275) missing from error log; got: {error_msgs}"
    )


def test_cost_tracking_failure_not_warning_level(caplog):
    """
    Failure must emit ERROR, not WARNING.  Before the fix logger.warning was used,
    which meant ops dashboards filtered on ERROR would miss cost data loss events.
    """
    mock_tracker = MagicMock()
    mock_tracker.record_request.side_effect = Exception("tracker crashed")

    orig_enabled = cost_mod._COST_TRACKING_ENABLED
    cost_mod._COST_TRACKING_ENABLED = True
    try:
        with caplog.at_level(logging.WARNING, logger="tokenpak.proxy.proxy"):
            with patch(
                "tokenpak.telemetry.cost_tracker.get_cost_tracker",
                return_value=mock_tracker,
            ):
                cost_mod.record_proxy_request(
                    model="claude-sonnet-4-5",
                    prompt_tokens=50,
                    completion_tokens=30,
                )
    finally:
        cost_mod._COST_TRACKING_ENABLED = orig_enabled

    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "cost" in r.getMessage().lower()
    ]
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Must have at least one ERROR-level record"
    assert not warning_records, "Must NOT emit a WARNING for tracker failure (use ERROR instead)"
