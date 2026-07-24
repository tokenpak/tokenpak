"""Contract tests for the savings-first ``tokenpak status`` renderer.

These tests exercise the current command implementation.  They intentionally
distinguish TokenPak-created savings from provider/client cache observations so
the CLI never credits TokenPak for cache work it did not perform.
"""

from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from tokenpak.cli.commands import status


def _health() -> dict:
    return {
        "status": "ok",
        "uptime_seconds": 3600,
    }


def _stats(
    *,
    requests: int = 10,
    sent: int = 70_000,
    saved: int = 30_000,
    cache_read: int = 20_000,
    cost: float = 0.50,
    cache_origin: dict[str, int] | None = None,
    errors: int = 0,
) -> dict:
    payload = {
        "session": {
            "requests": requests,
            "input_tokens": sent + saved,
            "sent_input_tokens": sent,
            "saved_tokens": saved,
            "protected_tokens": 0,
            "output_tokens": 2_000,
            "cost": cost,
            "errors": errors,
            "start_time": 0,
            "injected_tokens": 0,
            "injection_hits": 0,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": 0,
        }
    }
    if cache_origin is not None:
        payload["cache_read_by_origin"] = cache_origin
    return payload


def _historical(*, requests: int = 0) -> dict:
    return {
        "totals": {"requests": requests, "with_cost": 0.0},
        "models": [],
    }


def _tip_unavailable(**_kwargs) -> dict:
    return {
        "window": "all time",
        "lines": {},
        "source": "unavailable",
        "requests": 0,
        "reason": "no cache attribution observations",
    }


def _render(
    *,
    health: dict | None,
    stats: dict | None,
    cache: dict | None = None,
    historical: dict | None = None,
) -> str:
    responses = {
        "/health": health,
        "/stats": stats,
        "/cache-stats": cache,
    }

    def _fetch(url: str, timeout: int = 5):
        del timeout
        return next((value for suffix, value in responses.items() if url.endswith(suffix)), None)

    out = StringIO()
    with (
        patch.object(status, "_fetch", side_effect=_fetch),
        patch.object(
            status,
            "_calculate_fleet_savings",
            return_value=historical or {"error": "db_not_found"},
        ),
        patch.object(status, "_query_tip_cache_attribution", side_effect=_tip_unavailable),
        patch.object(status, "_connect_db", return_value=None),
        patch.object(status, "_print_free_tier_upgrade_hint"),
        patch.object(status, "_get_version", return_value="1.14.0"),
        redirect_stdout(out),
    ):
        status.run(no_meme=True)
    return out.getvalue()


def test_current_value_traffic_and_cache_sections_are_rendered():
    out = _render(health=_health(), stats=_stats())
    assert "Value Created (this session)" in out
    assert "Traffic" in out
    assert "Cache activity (observed)" in out
    assert "Healthy" in out


def test_compression_uses_sent_plus_avoided_tokens_as_denominator():
    out = _render(health=_health(), stats=_stats(sent=80_000, saved=20_000, cache_read=0))
    assert "20.0% token reduction" in out


def test_zero_compression_omits_compression_credit_line():
    out = _render(health=_health(), stats=_stats(saved=0))
    assert "Compression" not in out


def test_client_cache_is_observed_but_not_credited_to_tokenpak():
    out = _render(
        health=_health(),
        stats=_stats(
            saved=0,
            cache_read=50_000,
            cache_origin={"client": 50_000, "proxy": 0, "unknown": 0},
        ),
    )
    assert "client: 50.0K" in out
    assert "Proxy cache" not in out
    assert "Wire-side (proxy)             $0.00" in out


def test_proxy_owned_cache_is_credited_separately():
    out = _render(
        health=_health(),
        stats=_stats(
            saved=0,
            cache_read=50_000,
            cache_origin={"client": 0, "proxy": 50_000, "unknown": 0},
        ),
    )
    assert "Proxy cache" in out
    assert "proxy: 50.0K" in out


def test_unknown_cache_origin_is_reported_without_proxy_credit():
    out = _render(
        health=_health(),
        stats=_stats(
            saved=0,
            cache_read=12_000,
            cache_origin={"client": 0, "proxy": 0, "unknown": 12_000},
        ),
    )
    assert "unknown: 12.0K" in out
    assert "Proxy cache" not in out


def test_cache_rates_come_from_cache_observations():
    out = _render(
        health=_health(),
        stats=_stats(sent=80_000, cache_read=20_000),
        cache={"cache_hits": 3, "cache_misses": 1, "miss_reasons": {}},
    )
    assert "Token cache rate            20%" in out
    assert "Request hit rate            75%" in out
    assert "3 of 4 requests" in out


def test_no_cache_observations_are_not_fabricated_from_uptime():
    out = _render(health=_health(), stats=_stats(cache_read=0), cache=None)
    assert "Token cache rate             0%" in out
    assert "Request hit rate           n/a" in out
    assert "no cache observations" in out
    assert "Schema normalized" not in out


def test_removed_health_registry_observation_is_not_invented():
    out = _render(
        health={**_health(), "tool_schema_registry": {"schema_changes": 4}},
        stats=_stats(),
        cache={"cache_hits": 0, "cache_misses": 1, "miss_reasons": {}},
    )
    assert "Schema normalized" not in out


def test_proxy_errors_produce_actionable_health_message():
    out = _render(health=_health(), stats=_stats(errors=2))
    assert "2 error(s)" in out
    assert "tokenpak doctor" in out


def test_proxy_unreachable_without_database_fails_gracefully():
    out = _render(health=None, stats=None)
    assert "Proxy unreachable and no monitor database found" in out
    assert "tokenpak serve" in out
    assert "Traceback" not in out


def test_proxy_unreachable_can_render_historical_database_data():
    historical = {
        "totals": {"requests": 2, "with_cost": 0.25},
        "models": [
            {
                "model": "claude-sonnet-4-6",
                "requests": 2,
                "input_tokens": 1_000,
                "output_tokens": 100,
                "cache_read_tokens": 0,
                "compressed_tokens": 250,
            }
        ],
    }
    out = _render(health=None, stats=None, historical=historical)
    assert "Value Created (all time)" in out
    assert "Requests                      2" in out
    assert "Proxy unreachable" in out


def test_missing_live_stats_uses_historical_database_data():
    out = _render(health=_health(), stats=None, historical=_historical(requests=0))
    assert "Value Created (all time)" in out
    assert "Traceback" not in out
