"""Binding-runway and endpoint golden cases for session economics."""

from __future__ import annotations

import http.client
import json
from datetime import datetime, timezone
from pathlib import Path

from tests.proxy._proxy_subprocess import free_port
from tests.proxy.test_session_forecast_state import _create_ledger, _fresh_rates
from tokenpak.core.contracts.session_economics import (
    BindingConstraint,
    GuardState,
    RunwayStatus,
    SessionEconomics,
    ValueState,
)
from tokenpak.proxy import server as server_module
from tokenpak.proxy.server import ProxyServer
from tokenpak.proxy.session_forecast import _build_session_economics as build_session_economics
from tokenpak.proxy.spend_guard.policy import SpendGuardConfig

_NOW = datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc)


def _config(**overrides: object) -> SpendGuardConfig:
    config = SpendGuardConfig(
        warn_tokens=1_000_000_000,
        warn_cost_usd=0.0,
        block_cost_usd=0.0,
        hard_block_cost_usd=0.0,
        session_block_cost_usd=0.0,
        rolling_caps_enabled=False,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _two_turn_ledger(
    tmp_path: Path,
    *,
    first_input: int,
    second_input: int,
    output: int = 10,
    first_cost: float = 0.01,
    second_cost: float = 0.01,
    agent_id: str = "trix",
    second_agent_id: str | None = None,
    second_output: int | None = None,
) -> Path:
    final_agent_id = agent_id if second_agent_id is None else second_agent_id
    final_output = output if second_output is None else second_output
    return _create_ledger(
        tmp_path,
        [
            {
                "provider_usage_ref": "turn-1",
                "input_tokens": first_input,
                "output_tokens": output,
                "estimated_cost": first_cost,
                "provider_input_tokens": first_input,
                "provider_output_tokens": output,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "provider_cache_read_tokens": 0,
                "provider_cache_creation_tokens": 0,
                "agent_id": agent_id,
            },
            {
                "timestamp": "2026-08-10T12:01:00Z",
                "provider_usage_ref": "turn-2",
                "input_tokens": second_input,
                "output_tokens": final_output,
                "estimated_cost": second_cost,
                "provider_input_tokens": second_input,
                "provider_output_tokens": final_output,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "provider_cache_read_tokens": 0,
                "provider_cache_creation_tokens": 0,
                "agent_id": final_agent_id,
            },
        ],
    )


def test_context_soft_constraint_is_the_minimum_runway(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(tmp_path, first_input=60, second_input=80)
    monkeypatch.setattr("tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 200)

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(
            default_context_window_percent=50,
            hard_stop_context_window_percent=100,
        ),
        rate_provenance=_fresh_rates(),
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 1
    assert economics.runway.binding_constraint is BindingConstraint.CONTEXT_SOFT
    assert economics.runway.guard_state is GuardState.ALLOW


def test_unknown_model_fallback_uses_total_request_token_growth(
    tmp_path: Path, monkeypatch
) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=100,
        second_input=100,
        output=0,
        second_output=100,
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: None
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(block_tokens=350, hard_block_tokens=1_000),
        rate_provenance=_fresh_rates(),
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 2
    assert economics.runway.binding_constraint is BindingConstraint.CONTEXT_SOFT


def test_context_hard_stop_has_absolute_precedence(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=180,
        second_input=200,
        first_cost=2.0,
        second_cost=2.0,
    )
    monkeypatch.setattr("tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 200)
    config = _config(
        default_context_window_percent=50,
        hard_stop_context_window_percent=100,
        hard_block_cost_usd=1.0,
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=1.0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage=None,
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 0
    assert economics.runway.binding_constraint is BindingConstraint.CONTEXT_HARD
    assert economics.runway.guard_state is GuardState.HARD_STOP


def test_context_soft_block_and_warn_band_map_to_guard_state(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(tmp_path, first_input=80, second_input=110)
    monkeypatch.setattr("tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 200)

    blocked = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(
            default_context_window_percent=50,
            hard_stop_context_window_percent=100,
        ),
        rate_provenance=_fresh_rates(),
    )
    amber = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(
            warn_tokens=115,
            default_context_window_percent=90,
            hard_stop_context_window_percent=100,
        ),
        rate_provenance=_fresh_rates(),
    )

    assert blocked.runway.turns == 0
    assert blocked.runway.binding_constraint is BindingConstraint.CONTEXT_SOFT
    assert blocked.runway.guard_state is GuardState.SOFT_BLOCK
    assert amber.runway.turns is not None and amber.runway.turns > 0
    assert amber.runway.guard_state is GuardState.AMBER


def test_current_context_soft_block_does_not_require_a_learned_growth_rate(
    tmp_path: Path, monkeypatch
) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {
                "provider_usage_ref": "turn-1",
                "input_tokens": 100,
                "provider_input_tokens": 100,
                "output_tokens": 10,
                "provider_output_tokens": 10,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "provider_cache_read_tokens": 0,
                "provider_cache_creation_tokens": 0,
            }
        ],
    )
    monkeypatch.setattr("tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 200)

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(
            default_context_window_percent=50,
            hard_stop_context_window_percent=100,
        ),
        rate_provenance=_fresh_rates(),
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 0
    assert economics.runway.binding_constraint is BindingConstraint.CONTEXT_SOFT
    assert economics.runway.guard_state is GuardState.SOFT_BLOCK


def test_session_budget_constraint_can_bind_first(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=100,
        second_input=110,
        first_cost=2.0,
        second_cost=2.0,
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(session_block_cost_usd=10.0),
        rate_provenance=_fresh_rates(),
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 3
    assert economics.runway.binding_constraint is BindingConstraint.BUDGET


def test_session_budget_uses_the_configured_sliding_window(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=100,
        second_input=110,
        first_cost=2.0,
        second_cost=2.0,
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(
            session_block_cost_usd=10.0,
            session_window_seconds=90,
        ),
        rate_provenance=_fresh_rates(),
    )

    # At 12:02 only the 12:01 turn remains inside a 90-second window:
    # (10 - 2 currently used) / 2 USD EWMA = 4 turns.
    assert economics.runway.turns == 4
    assert economics.runway.binding_constraint is BindingConstraint.BUDGET


def test_per_request_dollar_hard_stop_binds_budget(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=100,
        second_input=110,
        first_cost=2.0,
        second_cost=2.0,
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(hard_block_cost_usd=1.0),
        rate_provenance=_fresh_rates(),
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 0
    assert economics.runway.binding_constraint is BindingConstraint.BUDGET
    assert economics.runway.guard_state is GuardState.HARD_STOP


def test_per_request_dollar_runway_learns_until_cost_growth_exists(
    tmp_path: Path, monkeypatch
) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=100,
        second_input=110,
        first_cost=2.0,
        second_cost=2.0,
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(block_cost_usd=10.0),
        rate_provenance=_fresh_rates(),
    )

    assert economics.runway.status is RunwayStatus.LEARNING
    assert economics.runway.turns is None
    assert "cost growth" in economics.runway.reason


def test_rolling_token_cap_constraint_can_bind_first(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(tmp_path, first_input=10, second_input=10, output=10)
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )
    config = _config(
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=0.0,
        rolling_caps_per_agent_max_tokens_total=100,
        rolling_caps_per_agent_max_cache_read_tokens=0,
        rolling_caps_per_fleet_max_cost_usd=0.0,
        rolling_caps_per_fleet_max_tokens_total=0,
        rolling_caps_per_fleet_max_cache_read_tokens=0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage={"agent_tokens_total": 90},
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 1
    assert economics.runway.binding_constraint is BindingConstraint.ROLLING_CAP


def test_per_agent_rolling_cap_without_attribution_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=10,
        second_input=10,
        output=10,
        agent_id="",
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )
    config = _config(
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=0.0,
        rolling_caps_per_agent_max_tokens_total=100,
        rolling_caps_per_agent_max_cache_read_tokens=0,
        rolling_caps_per_fleet_max_cost_usd=0.0,
        rolling_caps_per_fleet_max_tokens_total=0,
        rolling_caps_per_fleet_max_cache_read_tokens=0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage={"agent_tokens_total": 90},
    )

    assert economics.runway.status is RunwayStatus.UNAVAILABLE
    assert "agent attribution" in economics.runway.reason


def test_per_agent_rolling_cap_with_partial_attribution_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=10,
        second_input=10,
        output=10,
        second_agent_id="",
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )
    config = _config(
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=0.0,
        rolling_caps_per_agent_max_tokens_total=100,
        rolling_caps_per_agent_max_cache_read_tokens=0,
        rolling_caps_per_fleet_max_cost_usd=0.0,
        rolling_caps_per_fleet_max_tokens_total=0,
        rolling_caps_per_fleet_max_cache_read_tokens=0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage={"agent_tokens_total": 90},
    )

    assert economics.runway.status is RunwayStatus.UNAVAILABLE
    assert "agent attribution" in economics.runway.reason


def test_fleet_rolling_cap_remains_measurable_without_agent_attribution(
    tmp_path: Path, monkeypatch
) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=10,
        second_input=10,
        output=10,
        agent_id="",
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )
    config = _config(
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=0.0,
        rolling_caps_per_agent_max_tokens_total=0,
        rolling_caps_per_agent_max_cache_read_tokens=0,
        rolling_caps_per_fleet_max_cost_usd=0.0,
        rolling_caps_per_fleet_max_tokens_total=100,
        rolling_caps_per_fleet_max_cache_read_tokens=0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage={"fleet_tokens_total": 90},
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 1
    assert economics.runway.binding_constraint is BindingConstraint.ROLLING_CAP


def test_active_dollar_constraint_with_stale_pricing_is_unavailable(tmp_path: Path) -> None:
    db = _two_turn_ledger(
        tmp_path,
        first_input=100,
        second_input=110,
        first_cost=2.0,
        second_cost=2.0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=_config(session_block_cost_usd=10.0),
    )

    assert economics.facts.cost_usd.state is ValueState.UNAVAILABLE
    assert economics.facts.cost_usd.value is None
    assert economics.runway.status is RunwayStatus.UNAVAILABLE
    assert "fresh pricing" in economics.runway.reason


def test_unmeasurable_rolling_usage_is_an_error(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(tmp_path, first_input=10, second_input=10, output=10)
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )
    config = _config(
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=0.0,
        rolling_caps_per_agent_max_tokens_total=100,
        rolling_caps_per_agent_max_cache_read_tokens=0,
        rolling_caps_per_fleet_max_cost_usd=0.0,
        rolling_caps_per_fleet_max_tokens_total=0,
        rolling_caps_per_fleet_max_cache_read_tokens=0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage=None,
    )

    assert economics.runway.status is RunwayStatus.ERROR
    assert economics.runway.binding_constraint is BindingConstraint.ROLLING_CAP
    assert economics.runway.guard_state is GuardState.SOFT_BLOCK


def test_current_rolling_cap_block_does_not_require_a_learned_burn(
    tmp_path: Path, monkeypatch
) -> None:
    db = _create_ledger(
        tmp_path,
        [
            {
                "provider_usage_ref": "turn-1",
                "input_tokens": 10,
                "provider_input_tokens": 10,
                "output_tokens": 10,
                "provider_output_tokens": 10,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "provider_cache_read_tokens": 0,
                "provider_cache_creation_tokens": 0,
            }
        ],
    )
    monkeypatch.setattr(
        "tokenpak.proxy.session_forecast.get_model_max_context", lambda _model: 1_000_000
    )
    config = _config(
        rolling_caps_enabled=True,
        rolling_caps_per_agent_max_cost_usd=0.0,
        rolling_caps_per_agent_max_tokens_total=100,
        rolling_caps_per_agent_max_cache_read_tokens=0,
        rolling_caps_per_fleet_max_cost_usd=0.0,
        rolling_caps_per_fleet_max_tokens_total=0,
        rolling_caps_per_fleet_max_cache_read_tokens=0,
    )

    economics = build_session_economics(
        "session-golden",
        monitor_db_path=str(db),
        now=_NOW,
        spend_guard_config=config,
        rate_provenance=_fresh_rates(),
        rolling_usage={"agent_tokens_total": 100},
    )

    assert economics.runway.status is RunwayStatus.AVAILABLE
    assert economics.runway.turns == 0
    assert economics.runway.binding_constraint is BindingConstraint.ROLLING_CAP
    assert economics.runway.guard_state is GuardState.SOFT_BLOCK


def test_http_endpoint_serves_versioned_contract_without_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = _two_turn_ledger(tmp_path, first_input=100, second_input=120)
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "0")
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")

    http_server_type = server_module._ThreadedHTTPServer

    def bind_ephemeral(address, handler):
        return http_server_type((address[0], 0), handler)

    monkeypatch.setattr(server_module, "_ThreadedHTTPServer", bind_ephemeral)
    proxy = ProxyServer(host="127.0.0.1", port=free_port())
    proxy.start(blocking=False)
    assert proxy._server is not None
    port = int(proxy._server.server_address[1])
    body = json.dumps({"model": "claude-sonnet-4-5"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(
            "POST",
            "/v1/messages/session-economics",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Claude-Code-Session-Id": "session-golden",
            },
        )
        response = conn.getresponse()
        response_body = response.read()
    finally:
        conn.close()
        proxy.stop()

    assert response.status == 200
    economics = SessionEconomics.from_json(response_body.decode())
    assert economics.session.id == "session-golden"
    assert economics.session.turns_observed == 2
    assert economics.advisory is None


def test_http_endpoint_sanitizes_internal_ledger_failures(tmp_path: Path, monkeypatch) -> None:
    db = _two_turn_ledger(tmp_path, first_input=100, second_input=120)
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError("sensitive local path: /private/operator/ledger.db")

    monkeypatch.setattr(
        "tokenpak.proxy.forecast_endpoint._build_session_economics_response",
        fail_closed,
    )
    http_server_type = server_module._ThreadedHTTPServer

    def bind_ephemeral(address, handler):
        return http_server_type((address[0], 0), handler)

    monkeypatch.setattr(server_module, "_ThreadedHTTPServer", bind_ephemeral)
    proxy = ProxyServer(host="127.0.0.1", port=free_port())
    proxy.start(blocking=False)
    assert proxy._server is not None
    port = int(proxy._server.server_address[1])
    body = json.dumps({"session_id": "session-golden"}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(
            "POST",
            "/v1/messages/session-economics",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        response_body = response.read()
    finally:
        conn.close()
        proxy.stop()

    payload = json.loads(response_body)
    assert response.status == 500
    assert payload["error"]["type"] == "api_error"
    assert "local ledger" in payload["error"]["message"]
    assert "private/operator" not in response_body.decode()
