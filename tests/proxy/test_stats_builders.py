"""Regression: /health + /stats response builders must exist and be exercisable.

`routes.py` lazily imports `build_health_response`/`build_stats_response` from
`tokenpak.proxy.stats`; both defs were dropped in an earlier change, so hitting
GET /health or GET /stats raised ImportError. These tests fail with ImportError
against the broken tree and pass once the builders are restored.
"""

import json

from tokenpak.proxy.stats import build_health_response, build_stats_response


def test_build_health_response_returns_json_serialisable_dict():
    resp = build_health_response(
        session={"requests": 3, "canon_hits": 1},
        compilation_mode="balanced",
        vault_info={"available": True, "blocks": 12, "path": "/tmp/vault"},
        router_info={"backends": 2},
        router_enabled=True,
        capsule_available=True,
        canon_available=True,
        skeleton_enabled=False,
        shadow_enabled=False,
        budget_total_tokens=100000,
        tool_registry_stats={"tools": 5},
        tool_registry_available=True,
        term_resolver_enabled=False,
        term_resolver_available=False,
        term_resolver_top_k=8,
        term_resolver_max_bytes=2048,
        query_expansion_enabled=False,
        upstream_timeout=60,
        provider_circuits={"anthropic": {"open": False, "failures": 0}},
        request_latencies=[10, 20, 30, 40],
    )
    assert resp["status"] == "ok"
    assert resp["router"] == {"enabled": True, "backends": 2}
    assert resp["circuit_breakers"]["anthropic"] == {"open": False, "failures": 0}
    assert resp["stats"]["requests"] == 3
    assert resp["latency"]["samples"] == 4
    json.dumps(resp)  # must be JSON-serialisable


def test_build_health_response_handles_empty_latencies():
    resp = build_health_response(
        session={},
        compilation_mode="balanced",
        vault_info={"available": False, "blocks": 0, "path": ""},
        router_info={},
        router_enabled=False,
        capsule_available=False,
        canon_available=False,
        skeleton_enabled=False,
        shadow_enabled=False,
        budget_total_tokens=0,
        tool_registry_stats={},
        tool_registry_available=False,
        term_resolver_enabled=False,
        term_resolver_available=False,
        term_resolver_top_k=0,
        term_resolver_max_bytes=0,
        query_expansion_enabled=False,
        upstream_timeout=0,
        provider_circuits={},
        request_latencies=[],
    )
    assert resp["latency"] == {"p50_latency_ms": 0, "p99_latency_ms": 0, "samples": 0}
    json.dumps(resp)


def test_build_stats_response_returns_json_serialisable_dict():
    resp = build_stats_response(
        session={"requests": 7, "canon_hits": 2, "canon_tokens_saved": 99},
        compilation_mode="aggressive",
        vault_info={"available": True, "blocks": 4, "last_timing_ms": {}},
        router_enabled=True,
        capsule_available=False,
        compression_timeouts=1,
        max_compression_time_ms=500,
        canon_available=True,
        skeleton_enabled=True,
        shadow_enabled=False,
        budget_total_tokens=50000,
        monitor_today={"requests": 7},
        monitor_by_model={"claude": 7},
        monitor_recent=[{"id": 1}],
    )
    assert resp["session"]["requests"] == 7
    assert resp["canon"]["tokens_saved"] == 99
    assert resp["today"] == {"requests": 7}
    assert resp["recent"] == [{"id": 1}]
    json.dumps(resp)
