"""Actual monetary arithmetic, persisted binding and unknown-history refusal."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from tokenpak.models._registry import ModelRegistry
from tokenpak.proxy.spend_guard.request_pricing import RequestPrice, price_request
from tokenpak.proxy.spend_guard.reservation import ReservationUnavailable

from . import test_durable_reservations as storage_tests
from .pricing_fixtures import synthetic_price
from .test_durable_reservations import _caps, _log, _reserve
from .test_request_workload import response

domain = storage_tests.domain


def _workload(**changes):
    return replace(response(), **changes)


def _registry(*bands):
    registry = ModelRegistry()
    info = registry.resolve("claude-sonnet-4-6")
    registry._models[info.model_id] = replace(info, rate_bands=bands)
    return registry


def test_one_hour_creation_uses_observed_rate_and_inclusive_counts():
    workload = _workload(
        input_tokens=100,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=100,
        cache_ttl_seconds=3600,
    )
    price = price_request(workload)
    assert price.cost_usd == 0.000600
    assert price.bands[0].cache_write_per_mtok == 6
    assert RequestPrice.from_json(price.to_json()) == price


def test_unknown_ttl_zero_cache_charge_does_not_invent_a_lifetime():
    workload = _workload(
        input_tokens=100,
        output_tokens=10,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cache_ttl_seconds=None,
        reason_codes=("cache_ttl_unavailable",),
    )
    price = price_request(workload)
    assert price.cost_usd == 0.000450
    assert price.workload.cache_ttl_seconds is None
    assert {b.cache_ttl_seconds for b in price.bands} == {300, 3600}
    # Rates for unused cache components may differ; used components must agree.
    expensive = replace(price.bands[1], input_per_mtok=12)
    with pytest.raises(ValueError, match="changes token prices"):
        price_request(workload, registry=_registry(price.bands[0], expensive))
    with pytest.raises(ValueError, match="supported cache lifetimes"):
        price_request(workload, registry=_registry(price.bands[0]))


@pytest.mark.parametrize(
    "changes",
    [
        {"cache_ttl_seconds": None, "reason_codes": ("cache_ttl_unavailable",)},
        {"service_tier": None, "reason_codes": ("service_tier_unavailable",)},
        {"region": None, "reason_codes": ("region_unavailable",)},
        {"reason_codes": ("response_feature_unsupported",)},
        {"reason_codes": ("route_unsupported",)},
        {"response_complete": False, "reason_codes": ("response_incomplete",)},
    ],
)
def test_incomplete_or_unsupported_workload_has_no_scalar_fallback(changes):
    with pytest.raises(ValueError):
        price_request(_workload(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"verified_at": "2099-01-01T00:00:00Z"},
        {"verified_at": "2025-01-01T00:00:00Z", "expires_at": "2025-02-01T00:00:00Z"},
        {"source_url": "https://untrusted.example/prices"},
        {"source_url": "https://platform.claude.com/prices?credential=private"},
        {"regions": ("*",)},
        {"cache_write_per_mtok": None},
    ],
)
def test_unverified_or_expired_band_is_unpriced(changes):
    workload = _workload()
    band = price_request(workload).bands[0]
    with pytest.raises(ValueError):
        price_request(workload, registry=_registry(replace(band, **changes)))


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw[:-1] + ',"schema_version":"native-request-price/1"}',
        lambda raw: raw.replace('"native-request-price/1"', '"native-request-price/2"'),
        lambda raw: raw.replace('"output":2000000.0', '"output":1e999'),
        lambda raw: " " * 16385 + raw,
    ],
)
def test_receipt_strict_parsing(change):
    with pytest.raises(ValueError):
        RequestPrice.from_json(change(synthetic_price().to_json()))


@pytest.mark.parametrize("synchronous", [False, True])
def test_monitor_commits_price_with_money_and_settles(domain, synchronous):
    store, monitor = domain
    if synchronous:
        assert monitor.stop(timeout=3)
    ref, _ = _reserve(store)
    _log(monitor, ref, cost=6)
    with sqlite3.connect(monitor.db_path) as conn:
        row = conn.execute("SELECT estimated_cost, guard_price_json FROM requests").fetchone()
    assert row[0] == RequestPrice.from_json(row[1]).cost_usd == 6
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=9))[1]


@pytest.mark.parametrize("receipt", [None, "{}", '{"x": 1e999}'])
def test_unknown_history_cannot_admit_money_or_claim_eligible(domain, receipt):
    store, monitor = domain
    coverage = store.begin_request("session-a", "instance-a")
    ref, _ = _reserve(store)
    store.attempted_send(coverage, ref)
    _log(monitor, ref)
    store.finish_request(coverage)
    with sqlite3.connect(monitor.db_path) as conn:
        conn.execute("UPDATE requests SET guard_price_json=?", (receipt,))
    with pytest.raises(ReservationUnavailable, match="monetary pricing"):
        _reserve(store)
    # Count-only admission is distinct from certifying the recorded money.
    assert _reserve(store, caps=_caps(per_fleet_max_tokens_total=100))[0]
    snapshot = store.snapshot("session-a", 3600)
    assert not snapshot["guard_evidence_eligible"]
    assert "ledger_pricing_incomplete" in snapshot["reason_codes"]
    with sqlite3.connect(monitor.db_path) as conn:
        assert conn.execute("SELECT guard_price_json FROM requests").fetchone()[0] == receipt


@pytest.mark.parametrize(
    "column,value",
    [
        ("estimated_cost", 1),
        ("model", "other-model"),
        ("input_tokens", 4),
        ("cache_creation_tokens", 1),
        ("cache_read_tokens", 0),
        ("output_tokens", 1),
    ],
)
def test_edited_row_cannot_reuse_price_binding(domain, column, value):
    store, monitor = domain
    ref, _ = _reserve(store)
    _log(monitor, ref)
    with sqlite3.connect(monitor.db_path) as conn:
        conn.execute(f"UPDATE requests SET {column}=?", (value,))
    with pytest.raises(ReservationUnavailable, match="monetary pricing is invalid"):
        _reserve(store)


def test_monitor_rejects_mismatched_receipt_before_enqueue(domain):
    store, monitor = domain
    ref, _ = _reserve(store)
    with pytest.raises(ValueError, match="recorded cost"):
        monitor.log(
            model="test-model",
            input_tokens=3,
            output_tokens=2,
            cache_read_tokens=1,
            cost=1,
            latency_ms=1,
            status_code=200,
            endpoint="/v1/messages",
            reservation_ref=ref,
            guard_price_json=synthetic_price().to_json(),
        )
    assert monitor.flush(timeout=3)
    with sqlite3.connect(monitor.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    assert store.read_active("agent-a")["fleet_cost_usd"] == 4


def test_additive_monitor_migration_preserves_unpriced_history(domain):
    _, monitor = domain
    _log(monitor, None)
    assert monitor.stop(timeout=3)
    with sqlite3.connect(monitor.db_path) as conn:
        conn.execute("ALTER TABLE requests DROP COLUMN guard_price_json")
    from tokenpak.proxy.monitor import Monitor

    upgraded = Monitor(monitor.db_path)
    try:
        with sqlite3.connect(monitor.db_path) as conn:
            assert conn.execute(
                "SELECT estimated_cost, guard_price_json FROM requests"
            ).fetchone() == (4, None)
    finally:
        assert upgraded.stop(timeout=3)


def test_price_scan_deadline_covers_python_parsing(domain, monkeypatch):
    from tokenpak.proxy.spend_guard import reservation

    store, monitor = domain
    ref, _ = _reserve(store)
    _log(monitor, ref)
    monkeypatch.setattr(reservation.time, "monotonic", lambda: 10)
    with sqlite3.connect(monitor.db_path) as conn:
        with pytest.raises(ReservationUnavailable, match="pricing deadline"):
            reservation._validate_recorded_prices(conn, "2000-01-01", deadline=9)


def test_admission_includes_one_hour_and_long_context_rates(domain, monkeypatch):
    from types import SimpleNamespace

    from tokenpak.models import get_pricing
    from tokenpak.proxy.spend_guard.policy import SpendGuardConfig
    from tokenpak.proxy.spend_guard.request_accounting import RequestAccounting

    store, _ = domain
    info = get_pricing("claude-sonnet-4-6")
    # A synthetic expensive long-context band must bound a small request's
    # projection too: before sending, input/output and assigned tier can differ.
    expensive = replace(
        info.rate_bands[0],
        input_per_mtok=8,
        cache_write_per_mtok=12,
        output_per_mtok=30,
        min_input_tokens=200001,
        max_input_tokens=1000001,
    )
    monkeypatch.setattr(
        "tokenpak.models.get_pricing",
        lambda _: replace(info, rate_bands=(*info.rate_bands, expensive)),
    )
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.estimator.estimate",
        lambda *_: SimpleNamespace(projected_input_tokens=100, rates={"input": 3, "output": 15}),
    )
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard.policy.decide",
        lambda *_a, **_k: SimpleNamespace(decision="allow"),
    )
    accounting = object.__new__(RequestAccounting)
    accounting.store, accounting.ref = store, None
    accounting.session_id, accounting.agent_id, accounting.owner_id = (
        "session-a",
        "agent-a",
        "instance-a",
    )
    accounting.force = False
    accounting.config = SpendGuardConfig(
        rolling_caps_per_fleet_max_cost_usd=0.0014, rolling_caps_per_agent_max_cost_usd=0
    )
    body = json.dumps({"model": info.model_id, "max_tokens": 10, "messages": []}).encode()
    outcome = accounting.admit(
        body,
        info.model_id,
        "request-a",
        {"X-TokenPak-Session": "session-a", "X-TokenPak-Agent": "agent-a"},
    )
    assert outcome.kind == "block"
    error = json.loads(outcome.response_body)["error"]
    assert error["projected_add"] == 0.0015
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
