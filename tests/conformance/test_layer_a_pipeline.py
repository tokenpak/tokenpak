"""Layer A — in-process pipeline + emission validation.

Drives each proxy scenario through the production RouteClassifier +
LoopbackProvider + Monitor.log chokepoint, then schema-validates every
captured observer artifact against the registry schemas.

Scope: exercises the same emission call sites the real proxy uses.
Does NOT bring up the HTTP server (Layer C territory) and does NOT
drive services.request_pipeline stages (those live code paths don't
yet route telemetry emission through the pipeline stages — SC+1 /
P2-06..10 will change that).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tokenpak_tip_validator import validate_against

from tokenpak.core.contracts.capabilities import SELF_CAPABILITIES_PROXY
from tokenpak.proxy.monitor import Monitor
from tokenpak.services.diagnostics.conformance.loopback_provider import (
    get_loopback_provider,
)
from tokenpak.services.request import Request
from tokenpak.services.routing_service.classifier import get_classifier

from .conftest import (
    apply_scenario_env,
    load_proxy_scenario,
    proxy_scenario_names,
    restore_scenario_env,
)


pytestmark = pytest.mark.conformance


@pytest.mark.parametrize("scenario_name", proxy_scenario_names())
def test_scenario_classifies_and_dispatches(scenario_name, conformance_observer):
    """Scenario's request classifies to the expected route class + Loopback dispatches.

    Exercises:
    - RouteClassifier.classify on the scenario's synthetic Request.
    - LoopbackProvider.dispatch keyed by the classified route class.
    - expected_capabilities_intersection ⊆ SELF_CAPABILITIES_PROXY.
    """
    sc = load_proxy_scenario(scenario_name)
    saved_env = apply_scenario_env(sc)
    try:
        req = Request(
            body=sc["request"]["body_text"].encode(),
            headers=sc["request"]["headers"],
            metadata=dict(sc["request"].get("metadata") or {}),
        )
        actual = get_classifier().classify(req)
        assert actual.value == sc["expected_route_class"], (
            f"classify mismatch for {scenario_name}: "
            f"got {actual.value!r}, expected {sc['expected_route_class']!r}"
        )

        # LoopbackProvider wins when the route_class is in metadata.
        req.metadata["route_class"] = actual
        resp = get_loopback_provider().dispatch(req)
        assert resp.status == 200, f"loopback dispatch non-200 for {scenario_name}"
        assert resp.body, f"empty loopback body for {scenario_name}"

        # expected_capabilities_intersection is a documented subset of
        # the tip-proxy self-published set. This is the Policy-path
        # assertion (per DECISION-SC-06-B: not asserted on the row).
        exp_caps = frozenset(sc["expected_capabilities_intersection"])
        missing = exp_caps - SELF_CAPABILITIES_PROXY
        assert not missing, (
            f"scenario {scenario_name} declares capabilities not in "
            f"SELF_CAPABILITIES_PROXY: {sorted(missing)}"
        )
    finally:
        restore_scenario_env(saved_env)


@pytest.mark.parametrize("scenario_name", proxy_scenario_names())
def test_scenario_telemetry_row_validates(scenario_name, conformance_observer):
    """Monitor.log under the scenario emits an observer row that validates.

    Drives the SC-02 telemetry chokepoint with values derived from
    the scenario + LoopbackProvider usage. Asserts the captured row
    validates against telemetry-event.schema.json AND that
    cache_origin matches the scenario's declared expectation (when
    present).
    """
    sc = load_proxy_scenario(scenario_name)
    saved_env = apply_scenario_env(sc)
    try:
        req = Request(
            body=sc["request"]["body_text"].encode(),
            headers=sc["request"]["headers"],
            metadata=dict(sc["request"].get("metadata") or {}),
        )
        rc = get_classifier().classify(req)
        req.metadata["route_class"] = rc
        resp = get_loopback_provider().dispatch(req)
        usage = json.loads(resp.body)["usage"]

        expected_origin = sc.get("expected_cache_origin", "unknown")

        # `ignore_cleanup_errors=True` tolerates the race where
        # Monitor.log's async SQLite writer thread still holds files
        # in td when the context exits. The observer row (the thing
        # we assert on) is captured synchronously before teardown;
        # the disk artifact is incidental here. Python 3.12's rmtree
        # is stricter and surfaces this as OSError without the flag.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            m = Monitor(db_path=f"{td}/monitor.db")
            m.log(
                model=json.loads(resp.body).get("model", "unknown"),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost=0.0,
                latency_ms=42,
                status_code=resp.status,
                endpoint=sc["request"]["metadata"]["target_url"],
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_origin=expected_origin,
                request_id=f"test-{scenario_name}",
            )

        rows = conformance_observer["telemetry"]
        assert rows, f"no telemetry row captured for {scenario_name}"
        row = rows[-1]

        # Schema validation — the core SC assertion for Layer A.
        res = validate_against("telemetry-event", row)
        assert res.ok, (
            f"telemetry row failed schema for {scenario_name}: "
            f"{[(f.code, f.message) for f in res.errors()]}"
        )

        # cache_origin classification per DECISION-SC-06-B (Policy path).
        assert row["cache_origin"] == expected_origin, (
            f"{scenario_name}: cache_origin={row['cache_origin']!r}, "
            f"expected {expected_origin!r}"
        )

        # SC-03 request_id plumbing: observer sees the passed id verbatim.
        assert row["request_id"] == f"test-{scenario_name}"
    finally:
        restore_scenario_env(saved_env)


def test_openai_scenario_uses_narrowest_truthful_assertion(conformance_observer):
    """DECISION-SC-06-C: LoopbackProvider returns Anthropic-shaped bodies
    for every route class in SC-03 scope. For the OpenAI scenario, we
    assert only that tokens_in > 0 + schema validates, NOT that the
    body's parser-specific shape matches an OpenAI response. Loopback
    redesign is deferred per Kevin's constraint.
    """
    sc = load_proxy_scenario("openai_sdk_generic.json")
    saved_env = apply_scenario_env(sc)
    try:
        req = Request(
            body=sc["request"]["body_text"].encode(),
            headers=sc["request"]["headers"],
            metadata=dict(sc["request"].get("metadata") or {}),
        )
        rc = get_classifier().classify(req)
        assert rc.value == "openai-sdk"
        req.metadata["route_class"] = rc
        resp = get_loopback_provider().dispatch(req)
        usage = json.loads(resp.body)["usage"]

        # `ignore_cleanup_errors=True` tolerates the race where
        # Monitor.log's async SQLite writer thread still holds files
        # in td when the context exits. The observer row (the thing
        # we assert on) is captured synchronously before teardown;
        # the disk artifact is incidental here. Python 3.12's rmtree
        # is stricter and surfaces this as OSError without the flag.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            m = Monitor(db_path=f"{td}/monitor.db")
            m.log(
                model="gpt-4o-2024-08-06",
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cost=0.0,
                latency_ms=50,
                status_code=200,
                endpoint="https://api.openai.com/v1/chat/completions",
                cache_origin="client",
                request_id="test-openai",
            )

        row = conformance_observer["telemetry"][-1]
        assert row["tokens_in"] > 0
        assert validate_against("telemetry-event", row).ok
    finally:
        restore_scenario_env(saved_env)
