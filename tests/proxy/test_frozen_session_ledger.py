"""The canonical wrapper consumes a producer's frozen rows without rereading."""

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from tokenpak.proxy.forecast_endpoint import _build_session_economics_response
from tokenpak.proxy.spend_guard.session_state import _read_completed_session_rows

from .test_session_forecast_state import _config, _create_ledger, _fresh_rates


@pytest.mark.parametrize("rolling_usage", [None, {}])
def test_frozen_completed_rows_reach_actual_builder_without_live_reread(
    tmp_path, monkeypatch, rolling_usage
):
    path = _create_ledger(tmp_path, [{}])
    frozen = _read_completed_session_rows("session-golden", monitor_db_path=str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE requests SET estimated_cost=123")

    def forbidden(*_args, **_kwargs):
        pytest.fail("bounded producer's frozen ledger was replaced by a live reread")

    monkeypatch.setattr("tokenpak.proxy.session_forecast._read_completed_session_rows", forbidden)
    result = _build_session_economics_response(
        "session-golden",
        path,
        now=datetime(2026, 8, 10, 12, 3, tzinfo=timezone.utc),
        spend_guard_config=_config(),
        rate_provenance=_fresh_rates(),
        rolling_usage=rolling_usage,
        ledger_read=frozen,
    )
    assert result.session.turns_observed == 1
    assert result.facts.cost_usd.value == 0.01


def test_arbitrary_mapping_is_not_a_frozen_session_ledger(tmp_path):
    with pytest.raises(ValueError, match="canonical frozen"):
        _build_session_economics_response("session", tmp_path / "absent.db", ledger_read={})


def test_frozen_ledger_keeps_explicit_session_and_immutable_container(tmp_path):
    path = _create_ledger(tmp_path, [{}])
    frozen = _read_completed_session_rows("session-golden", monitor_db_path=str(path))
    with pytest.raises(ValueError, match="another session"):
        _build_session_economics_response("different-session", path, ledger_read=frozen)
    with pytest.raises(ValueError, match="canonical frozen"):
        _build_session_economics_response(
            "session-golden", path, ledger_read=replace(frozen, rows=list(frozen.rows))
        )
