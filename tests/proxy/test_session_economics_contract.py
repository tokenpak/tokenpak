from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tokenpak.core.contracts.session_economics as session_economics_module
from tokenpak.core.contracts import (
    SESSION_ECONOMICS_SCHEMA_VERSION,
    SessionEconomics,
    SessionEconomicsContractError,
)
from tokenpak.proxy import forecast_endpoint


def test_request_forecast_keeps_its_existing_response_contract(monkeypatch) -> None:
    monkeypatch.setattr(forecast_endpoint, "count_request_tokens", lambda body: 100)
    monkeypatch.setattr(
        forecast_endpoint,
        "estimate_cache_hit_likelihood",
        lambda model, db_path, session_id="": 0.25,
    )
    monkeypatch.setattr(
        forecast_endpoint,
        "estimate_ttfb_ms",
        lambda model, input_tokens, db_path: 175,
    )
    monkeypatch.setattr(
        "tokenpak.proxy.router.estimate_cost",
        lambda model, input_tokens, output_tokens, **kwargs: 0.1234567,
    )

    response = forecast_endpoint.build_forecast_response(
        {"model": "provider/model", "messages": []},
        ":memory:",
        session_id="session-1",
    )

    assert response == {
        "estimated_cost_usd": 0.123457,
        "input_tokens": 100,
        "cached_tokens": 25,
        "ttfb_estimate_ms": 175,
        "cache_hit_likelihood": 0.25,
        "model": "provider/model",
        "breakdown": {
            "input_tokens": 100,
            "output_estimate": 500,
            "cache_hits_estimate": 25,
            "cache_creates_estimate": 0,
        },
    }
    assert "schema_version" not in response
    assert "advisory" not in response


def test_request_forecast_is_not_accepted_as_session_economics(monkeypatch) -> None:
    monkeypatch.setattr(forecast_endpoint, "count_request_tokens", lambda body: 1)
    monkeypatch.setattr(
        forecast_endpoint,
        "estimate_cache_hit_likelihood",
        lambda model, db_path, session_id="": 0.0,
    )
    monkeypatch.setattr(
        forecast_endpoint,
        "estimate_ttfb_ms",
        lambda model, input_tokens, db_path: 100,
    )
    monkeypatch.setattr("tokenpak.proxy.router.estimate_cost", lambda *args, **kwargs: 0.0)

    response = forecast_endpoint.build_forecast_response(
        {"model": "provider/model", "messages": []}, ":memory:"
    )

    with pytest.raises(SessionEconomicsContractError):
        SessionEconomics.from_dict(response)


def test_shared_contract_export_names_version() -> None:
    assert SESSION_ECONOMICS_SCHEMA_VERSION == "session-economics/1"
    assert SessionEconomics.__module__ == "tokenpak.core.contracts.session_economics"


def test_shared_contract_has_no_paid_package_import() -> None:
    source = Path(session_economics_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    assert not any(name.startswith("tokenpak_paid") for name in imported_modules)
