# SPDX-License-Identifier: Apache-2.0
"""Shared CLI consumer tests for the canonical health contract."""

from __future__ import annotations

from types import SimpleNamespace

from tokenpak import _cli_core
from tokenpak._cli_core import _get_active_providers, _get_cache_hit_rate
from tokenpak.cli.commands import status as status_command
from tokenpak.cli.commands.doctor import _validate_canonical_health
from tokenpak.telemetry import query_dsl


def test_active_providers_ignores_circuit_breaker_envelope_metadata() -> None:
    health = {
        "circuit_breakers": {
            "enabled": True,
            "any_open": False,
            "providers": {
                "anthropic": {"state": "closed"},
                "openai": {"state": "open"},
            },
        }
    }

    assert _get_active_providers(health) == ["anthropic", "openai"]


def test_cache_hit_rate_preserves_no_observations_as_unavailable() -> None:
    assert _get_cache_hit_rate({"cache_hits": 0, "cache_misses": 0}) is None
    assert _get_cache_hit_rate({}) is None
    assert _get_cache_hit_rate({"cache_hits": 3, "cache_misses": 1}) == 75.0
    assert _get_cache_hit_rate({"cache_hits": "3", "cache_misses": 1}) is None
    assert _get_cache_hit_rate({"cache_hits": 3}) is None


def _canonical_health() -> dict[str, object]:
    return {
        "status": "ok",
        "uptime_seconds": 1,
        "version": "1.14.0",
        "requests_total": 0,
        "requests_errors": 0,
        "compression_ratio_avg": 1.0,
        "is_degraded": False,
        "is_shutting_down": False,
        "in_flight_requests": 0,
        "memory_guard": {},
        "admission": {},
        "agent_concurrency": {},
        "timestamp": "2026-07-23T00:00:00Z",
        "connection_pool": {},
        "circuit_breakers": {},
    }


def test_doctor_health_validation_rejects_unknown_missing_and_wrong_types() -> None:
    valid, reason = _validate_canonical_health(_canonical_health())
    assert valid is True
    assert reason == ""

    unknown = _canonical_health()
    unknown["status"] = "unknown"
    assert _validate_canonical_health(unknown)[0] is False

    missing = _canonical_health()
    del missing["requests_errors"]
    assert _validate_canonical_health(missing)[0] is False

    malformed = _canonical_health()
    malformed["requests_total"] = "0"
    assert _validate_canonical_health(malformed)[0] is False


def test_legacy_core_status_does_not_fabricate_missing_stats(monkeypatch, capsys) -> None:
    health = _canonical_health()

    def proxy_get(path: str):
        return health if path == "/health" else None

    monkeypatch.setattr(_cli_core, "_proxy_get", proxy_get)

    def telemetry_unavailable(*_args, **_kwargs):
        raise OSError("telemetry unavailable")

    monkeypatch.setattr(query_dsl, "get_savings_report", telemetry_unavailable)
    _cli_core._cmd_status_legacy(SimpleNamespace(output="normal", minimal=False))
    output = capsys.readouterr().out

    assert "Tokens in:       unknown" in output
    assert "Tokens saved:    unknown (unknown)" in output
    assert "Cost:            unknown" in output
    assert "Savings: unavailable" in output


def test_full_status_does_not_fabricate_missing_session_or_mode(monkeypatch, capsys) -> None:
    health = _canonical_health()

    def fetch(url: str, timeout: int = 5):
        del timeout
        return health if url.endswith("/health") else None

    monkeypatch.setattr(status_command, "_fetch", fetch)
    status_command.run_full(proxy_base="http://127.0.0.1:8766")
    output = capsys.readouterr().out

    assert "Session Requests:" in output and "unknown" in output
    assert "Cost (this session):" in output and "unknown" in output
    assert "hybrid mode" not in output
