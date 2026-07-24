# SPDX-License-Identifier: Apache-2.0
"""Health-contract regressions for the optimize command."""

from __future__ import annotations

from unittest.mock import patch

from tokenpak.cli.commands.optimize import _analyze_model


@patch("tokenpak.cli.commands.optimize._db_connect", return_value=None)
@patch("tokenpak.cli.commands.optimize._proxy_get")
def test_model_attribution_unavailable_is_not_inferred_from_health(
    mock_proxy_get, _mock_db
) -> None:
    result = _analyze_model({"session_requests": 2, "total_cost": 0.20})

    assert result["current_model"] is None
    assert result["cost_per_request"] == 0.1
    assert result["best_alternative"] is None
    mock_proxy_get.assert_not_called()
